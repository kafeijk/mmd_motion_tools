from contextlib import contextmanager

from bpy_extras.io_utils import ImportHelper

from .motion_fixer_operators import MotionBaseOperator
from ..utils import *


class ImportVmdPhysicsOperator(bpy.types.Operator, ImportHelper):
    bl_idname = "mmd_motion_tools.import_vmd_physics"
    bl_label = "导入物理"
    bl_description = "仅导入VMD文件中的物理骨骼关键帧"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".vmd"
    filter_glob: bpy.props.StringProperty(default="*.vmd", options={"HIDDEN"})

    scale: bpy.props.FloatProperty(
        name="Scale",
        description="Scaling factor for importing the motion",
        default=0.08,
    )
    margin: bpy.props.IntProperty(
        name="Margin",
        description="Number of frames to add before the motion starts (only applies if current frame is 0 or 1)",
        min=0,
        default=0,
    )

    @classmethod
    def poll(cls, context):
        if not is_mmd_tools_enabled():
            return False
        obj = context.active_object
        if obj is None:
            return False
        root = find_pmx_root_with_child(obj)
        if root is None:
            return False
        return True

    def check_props(self, context):
        obj = context.active_object
        root = find_pmx_root_with_child(obj)
        physical_bones = get_physical_bone(root)
        if not physical_bones:
            self.report({'ERROR'}, "当前模型不存在受物理影响骨骼!")
            return False

        physical_bones = set(physical_bones)

        bone_jp_names = get_vmd_bones(self.filepath)
        bone_bl_names = [convertNameToLR(n) for n in bone_jp_names]

        found = False
        for bl_name in bone_bl_names:
            if bl_name in physical_bones:
                found = True
                break
        if not found:
            self.report({'ERROR'}, "vmd文件中不存在物理骨骼关键帧!请使用新版MMD桥烘焙得到的文件进行导入")
            return False

        return True

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "scale")
        layout.prop(self, "margin")

    def execute(self, context):
        flag = self.check_props(context)
        if not flag:
            return {'CANCELLED'}

        # 获取源对象
        obj = context.active_object
        root = find_pmx_root_with_child(obj)
        armature = find_pmx_armature(root)

        # 显示源对象
        show_object(root)
        show_object(armature)

        # 拷贝骨架
        copied_armature = copy_obj(armature)
        copied_armature.parent = None
        deselect_all_objects()
        select_and_activate(copied_armature)

        # 导入动作
        bpy.ops.mmd_tools.import_vmd(
            'EXEC_DEFAULT',
            filepath=self.filepath,
            scale=self.scale,
            margin=self.margin,
            create_new_action=True,
        )

        # 拷贝物理数据
        physical_bones = get_physical_bone(root)
        copy_bone_animation(copied_armature, armature, physical_bones)
        # 通过物理骨骼的 IK 约束查找对应的 IK，并清除 IK 动画、关闭 IK
        set_physical_bone_ik(armature, physical_bones)

        # 移除 copied_armature
        bpy.data.objects.remove(copied_armature, do_unlink=True)
        bpy.ops.outliner.orphans_purge(do_local_ids=True, do_linked_ids=True, do_recursive=True)

        return {'FINISHED'}


class RemovePhysicalBoneAnimationOperator(bpy.types.Operator):
    bl_idname = "mmd_motion_tools.remove_physical_bone_animation"
    bl_label = "移除物理"
    bl_description = "移除当前模型物理骨骼的动画关键帧"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        if not is_mmd_tools_enabled():
            return False

        obj = context.active_object
        if obj is None:
            return False

        root = find_pmx_root_with_child(obj)
        if root is None:
            return False

        armature = find_pmx_armature(root)
        if armature is None:
            return False

        return True

    def execute(self, context):
        if not self.check_props(context):
            return {'CANCELLED'}

        flag = self.remove_physical_bone_animation(context)

        if not flag:
            return {'CANCELLED'}

        return {'FINISHED'}

    def check_props(self, context):
        obj = context.active_object
        root = find_pmx_root_with_child(obj)
        armature = find_pmx_armature(root)

        # 获取物理骨骼
        physical_bones = get_physical_bone(root)
        if not physical_bones:
            self.report({'ERROR'}, "当前模型不存在受物理影响骨骼!")
            return False

        action = armature.animation_data.action if armature.animation_data else None
        if not action:
            self.report({'ERROR'}, "当前骨架不存在动画!")
            return False

        return True

    def remove_physical_bone_animation(self, context):
        """移除指定物理骨骼的所有动画关键帧"""
        obj = context.active_object
        root = find_pmx_root_with_child(obj)
        armature = find_pmx_armature(root)
        action = armature.animation_data.action if armature.animation_data else None

        physical_bones = get_physical_bone(root)
        physical_bones = set(physical_bones)

        remove_fcurves = []
        remove_bone_names = set()
        for fc in action.fcurves:
            data_path = fc.data_path

            # 只处理骨骼动画
            if not data_path.startswith('pose.bones["'):
                continue

            # 获取骨骼名称
            bone_name = data_path.split('"', 2)[1]

            if bone_name in physical_bones:
                remove_fcurves.append(fc)
                remove_bone_names.add(bone_name)

        for fc in remove_fcurves:
            action.fcurves.remove(fc)

        set_physical_bone_ik(armature, physical_bones, enabled=True)

        self.report({'INFO'}, f"已移除 {len(remove_bone_names)} 个物理骨骼的动画")

        return True


def get_vmd_bones(filepath):
    """读取 VMD 文件，获取其中包含关键帧的骨骼名称"""
    try:
        from mmd_tools.core import vmd
    except ImportError:
        try:
            from bl_ext.blender_org.mmd_tools.core import vmd
        except ImportError:
            return None

    vmd_file = vmd.File()
    vmd_file.load(filepath=filepath)

    return list(vmd_file.boneAnimation.keys())


def copy_bone_animation(src_armature, dst_armature, bone_names):
    """将指定骨骼的动画从源 Action 完整覆盖到目标 Action。"""

    src_action = (
        src_armature.animation_data.action
        if src_armature.animation_data
        else None
    )

    if not src_action:
        return

    dst_action = (
        dst_armature.animation_data.action
        if dst_armature.animation_data
        else None
    )

    if not dst_action:
        dst_armature.animation_data_create()
        dst_action = bpy.data.actions.new(
            name=f"{dst_armature.name}_Action"
        )
        dst_armature.animation_data.action = dst_action

    bone_names = set(bone_names)

    # 缓存目标 F-Curve
    dst_fcurves = {
        (fc.data_path, fc.array_index): fc
        for fc in dst_action.fcurves
    }

    for fc in src_action.fcurves:
        data_path = fc.data_path

        # 只处理骨骼动画
        if not data_path.startswith('pose.bones["'):
            continue

        # 获取骨骼名称
        bone_name = data_path.split('"', 2)[1]

        if bone_name not in bone_names:
            continue

        key = (data_path, fc.array_index)
        new_fc = dst_fcurves.get(key)

        if new_fc is None:
            new_fc = dst_action.fcurves.new(
                data_path=data_path,
                index=fc.array_index,
            )
            dst_fcurves[key] = new_fc
        else:
            # 完整覆盖目标 F-Curve
            new_fc.keyframe_points.clear()

        src_kps = fc.keyframe_points
        count = len(src_kps)

        if count == 0:
            continue

        dst_kps = new_fc.keyframe_points
        dst_kps.add(count)

        # 批量复制关键帧坐标
        dst_kps.foreach_set(
            "co",
            [value for kp in src_kps for value in kp.co],
        )

        # 批量复制左右 Handle 坐标
        dst_kps.foreach_set(
            "handle_left",
            [value for kp in src_kps for value in kp.handle_left],
        )

        dst_kps.foreach_set(
            "handle_right",
            [value for kp in src_kps for value in kp.handle_right],
        )

        # 插值、Handle 类型是字符串枚举，不能使用 foreach_set
        for src_kp, dst_kp in zip(src_kps, dst_kps):
            dst_kp.interpolation = src_kp.interpolation
            dst_kp.handle_left_type = src_kp.handle_left_type
            dst_kp.handle_right_type = src_kp.handle_right_type

        new_fc.update()


def set_physical_bone_ik(armature, physical_bones, enabled=False):
    """通过物理骨骼的 IK 约束查找对应的 IK，并清除 IK 动画、设置 IK 启用状态"""
    physical_bones = set(physical_bones)
    pose_bones = armature.pose.bones

    # 获取需要关闭的 IK 骨骼
    ik_names = set()
    for pb in pose_bones:
        if pb.name not in physical_bones:
            continue

        for constraint in pb.constraints:
            if constraint.type != 'IK':
                continue

            if constraint.target != armature:
                continue

            subtarget = constraint.subtarget
            if subtarget and subtarget in pose_bones:
                ik_names.add(subtarget)

    if not ik_names:
        return

    # 清除 IK 动画
    action = (
        armature.animation_data.action
        if armature.animation_data
        else None
    )

    if action:
        data_paths = {
            f'pose.bones["{ik_name}"].mmd_ik_toggle'
            for ik_name in ik_names
        }

        for fc in list(action.fcurves):
            if fc.data_path in data_paths:
                action.fcurves.remove(fc)

    # 关闭 IK
    for ik_name in ik_names:
        pose_bones[ik_name].mmd_ik_toggle = enabled


class FindRepeatedMotionBaseOperator(MotionBaseOperator):
    """查找重复动作基础类"""

    threshold: bpy.props.IntProperty(
        name="最小连续重复帧数",
        description="连续重复的最少帧数",
        default=20,  # 更小则无意义，更大可能导致关键帧数量不足
        min=2,
    )

    error: bpy.props.FloatProperty(
        name="最大变化幅度",
        description="前后两帧动作允许的最大变化幅度",
        default=100,
        min=0,
        max=100,
        subtype='PERCENTAGE',
        precision=0,
        step=1,
    )

    def execute(self, context):
        props = context.scene.mmd_motion_tools_find_repeated_motion
        props.repeated_index = -1
        result = self.check_basic(context)
        if not result:
            return {'CANCELLED'}

        armature, bones = result
        bones = [pb for pb in bones if is_pose_bone_selected(pb)]

        if len(bones) > 20:
            self.report({'ERROR'}, '最多只能选择20个骨骼!')
            return {'CANCELLED'}

        action = armature.animation_data.action
        keyframes = self.get_keyframes(action, bones)

        if not keyframes:
            self.report({'ERROR'}, '所选骨骼无关键帧！')
            return {'CANCELLED'}

        frame_start = min(keyframes)
        frame_end = max(keyframes)
        if frame_end - frame_start + 1 < self.threshold:
            self.report({'ERROR'}, '关键帧范围不足!')
            return {'CANCELLED'}

        repeated = self.find_repeated(props, keyframes, bones, action)

        if not repeated:
            self.report({'INFO'}, '未找到重复动作!')
            return {'FINISHED'}

        props.repeated.clear()
        repeated_frames = set()
        involved_frames = set()
        for r in repeated:
            item = props.repeated.add()
            item.start = r[0]
            item.end = r[1]
            item.repeat_start = r[2]
            item.repeat_end = r[3]

            repeated_frames.update(range(r[0], r[1] + 1))

            involved_frames.update(range(r[0], r[1] + 1))
            involved_frames.update(range(r[2], r[3] + 1))

        self.report({'INFO'},
                    f'找到 {len(repeated)} 组重复动作，重复 {len(repeated_frames)} 帧，共涉及 {len(involved_frames)} 帧动作')
        return {'FINISHED'}

    def build_signature_map(self, props, motion_fcurves, bones, keyframes, signatures):
        """构建 signature -> 帧列表 的映射，同时填充关键帧处的 signatures。由子类实现查找策略"""
        raise NotImplementedError

    def get_keyframes(self, action, bones):
        frames = set()

        for pb in bones:
            path = f'pose.bones["{pb.name}"]'

            for fc in action.fcurves:
                if not fc.data_path.startswith(path):
                    continue

                for kp in fc.keyframe_points:
                    frames.add(int(kp.co[0]))

        return sorted(frames)

    def build_motion_fcurve_map(self, action, bones):
        pattern = re.compile(r'pose\.bones\["(.+?)"\]\.(.+)')
        bone_names = {pb.name for pb in bones}
        valid_props = {"rotation_quaternion", "location", }

        result = {}
        for fc in action.fcurves:
            match = pattern.match(fc.data_path)
            if not match:
                continue

            bone_name = match.group(1)
            prop = match.group(2)
            if bone_name not in bone_names:
                continue
            if prop not in valid_props:
                continue

            result[(prop, bone_name, fc.array_index)] = fc

        return result

    def get_signature(self, motion_fcurves, bones, frame):
        values = []
        for pb in bones:
            # rotation_quaternion 4个分量
            for index in range(4):
                fc = motion_fcurves.get(("rotation_quaternion", pb.name, index))
                if fc:
                    values.append(fc.evaluate(frame))
                else:
                    values.append(0.0)

            # location 3个分量
            for index in range(3):
                fc = motion_fcurves.get(("location", pb.name, index))
                if fc:
                    values.append(fc.evaluate(frame))
                else:
                    values.append(0.0)

        return tuple(values)

    def compare_signature(self, props, a, b):
        """比较两个动作状态是否接近"""
        if len(a) != len(b):
            return False

        for x, y in zip(a, b):
            if x == 0:
                if y != 0:
                    return False
                continue

            if abs(x - y) / abs(x) > (self.error / 100):
                return False

        return True

    @timeit
    def find_repeated(self, props, keyframes, bones, action):
        """寻找重复帧。首先筛选出存在重复的单帧，再以此为起点向后匹配连续重复帧"""
        frame_start = min(keyframes)
        frame_end = max(keyframes)
        threshold = self.threshold

        # 存储每一帧的动作状态
        signatures = [None] * (frame_end + 1)

        # 构建 (prop, bone_name, fc.array_index) -> fcurve 的映射
        motion_fcurves = self.build_motion_fcurve_map(action, bones)

        # signature_map 用于寻找重复的单帧，可以避免在一开始就进行全量帧的比对
        signature_map = self.build_signature_map(props, motion_fcurves, bones, keyframes, signatures)

        # 非关键帧处signature的获取，signatures 用于寻找重复的连续帧
        keyframe_set = set(keyframes)
        for i in range(frame_start, frame_end + 1):
            if i in keyframe_set:
                continue
            signature = self.get_signature(motion_fcurves, bones, i)
            signatures[i] = signature

        # 过滤掉 signature 未出现重复的项
        signature_map = {
            signature: frame_list
            for signature, frame_list in signature_map.items()
            if len(frame_list) > 1}

        repeated_pairs = []
        for signature, same_frames in signature_map.items():
            count = len(same_frames)
            for i in range(count):
                frame_a = same_frames[i]
                for j in range(i + 1, count):
                    frame_b = same_frames[j]

                    length = 1
                    while frame_a + length <= frame_end and frame_b + length <= frame_end:
                        next_a = frame_a + length
                        next_b = frame_b + length
                        sig_a = signatures[next_a]
                        sig_b = signatures[next_b]

                        if not self.compare_signature(props, sig_a, sig_b):
                            break

                        length += 1

                    if length >= threshold:
                        repeated_pairs.append({
                            "start": frame_a,
                            "end": frame_a + length - 1,
                            "repeat_start": frame_b,
                            "repeat_end": frame_b + length - 1
                        })
        print(f"repeated_pairs:{len(repeated_pairs)}")
        return self.merge_repeated(repeated_pairs)

    def merge_repeated(self, repeated_pairs):
        """去除被更大重复区间包含的小重复区间，保留最大重复范围"""
        if not repeated_pairs:
            return []

        # 按重复长度排序，大的优先
        repeated_pairs.sort(key=lambda x: (x["end"] - x["start"], x["repeat_end"] - x["repeat_start"]), reverse=True)

        filtered = []
        for curr in repeated_pairs:
            contained = False
            for exist in filtered:
                # 第一段被包含
                first_inside = exist["start"] <= curr["start"] and exist["end"] >= curr["end"]
                # 第二段被包含
                second_inside = exist["repeat_start"] <= curr["repeat_start"] and exist["repeat_end"] >= curr[
                    "repeat_end"]

                if first_inside and second_inside:
                    contained = True
                    break

                # 如果 curr 比已经保存的 exist 范围更大，则删除小范围的 exist，把 curr 保留下来。
                first_inside = curr["start"] <= exist["start"] and curr["end"] >= exist["end"]
                second_inside = curr["repeat_start"] <= exist["repeat_start"] and curr["repeat_end"] >= exist[
                    "repeat_end"]

                if first_inside and second_inside:
                    filtered.remove(exist)
                    break

            if not contained:
                filtered.append(curr)

        filtered = [(x["start"], x["end"], x["repeat_start"], x["repeat_end"]) for x in filtered]
        # 先按照 start 升序，如果 start 一致，则按照连续帧数量排序
        filtered.sort(key=lambda x: (x[0], x[1] - x[0]))

        return filtered


class FindRepeatedMotionOperator(FindRepeatedMotionBaseOperator, bpy.types.Operator):
    bl_idname = "mmd_motion_tools.find_repeated_motion"
    bl_label = "查找"
    bl_description = "查找指定骨骼中连续重复的动作帧，仅匹配完全相同的动作状态，速度快"
    bl_options = {'REGISTER', 'UNDO'}

    def build_signature_map(self, props, motion_fcurves, bones, keyframes, signatures):
        """普通查找：signature 严格相等才视为重复"""
        signature_map = {}

        for frame in keyframes:
            signature = self.get_signature(motion_fcurves, bones, frame)
            signatures[frame] = signature
            signature_map.setdefault(signature, []).append(frame)

        return signature_map


class FindRepeatedMotionStrongOperator(FindRepeatedMotionBaseOperator, bpy.types.Operator):
    bl_idname = "mmd_motion_tools.find_repeated_motion_strong"
    bl_label = "强力查找"
    bl_description = "查找单个骨骼中连续重复的动作帧，适用于动补等全帧插入的情形，速度慢"
    bl_options = {'REGISTER', 'UNDO'}

    threshold: bpy.props.IntProperty(
        name="最小连续重复帧数",
        description="连续重复的最少帧数",
        default=100,  # 更小则无意义，更大可能导致关键帧数量不足
        min=2,
    )

    def check_basic(self, context):
        """强力查找需要与已有signature逐一模糊比对，只能选中一个骨骼"""
        result = super().check_basic(context)
        if not result:
            return None

        armature, bones = result

        if len(bones) != 1:
            self.report({'ERROR'}, '强力查找只支持选中一个骨骼!')
            return None

        return armature, bones

    def build_signature_map(self, props, motion_fcurves, bones, keyframes, signatures):
        """强力查找：与已有 signature 做模糊比对，在误差范围内即视为重复"""
        signature_map = {}

        for frame in keyframes:
            signature = self.get_signature(motion_fcurves, bones, frame)
            signatures[frame] = signature

            matched = False

            for existing_signature, frames in signature_map.items():
                if self.compare_signature(props, existing_signature, signature):
                    frames.append(frame)
                    matched = True
                    break

            if not matched:
                signature_map[signature] = [frame]

        return signature_map


class JumpFrameOperator(bpy.types.Operator):
    bl_idname = "mmd_motion_tools.jump_frame"
    bl_label = "跳转帧"
    bl_description = "跳转到指定关键帧"
    bl_options = {'UNDO'}

    frame: bpy.props.IntProperty()

    def execute(self, context):
        context.scene.frame_set(self.frame)
        return {'FINISHED'}


class ClearRepeatedOperator(bpy.types.Operator):
    bl_idname = "mmd_motion_tools.clear_repeated"
    bl_label = "清除"
    bl_description = "清除查找重复帧结果"
    bl_options = {'UNDO'}

    def execute(self, context):
        props = context.scene.mmd_motion_tools_find_repeated_motion
        props.repeated.clear()
        return {'FINISHED'}


@contextmanager
def dopesheet_override(context):
    """
    找 Dope Sheet，没有就临时把一个区域切成 Dope Sheet，退出时恢复

    Window：操作系统层面的一个 Blender 窗口。可以有多个，比如用 新建窗口 分出去的。
    Screen：一个窗口里的整套布局，也就是工作区（Layout、Animation 等标签页）。
    Area：Screen 被分割出的一块块编辑器，比如 3D 视图、Dope Sheet、时间线。area.type 决定它是哪种编辑器，同一个 type 可能有不同 mode。
    Region：Area 内部的功能分区。WINDOW 是主绘图区，HEADER、CHANNELS、UI 等是周围的辅助区域。
    """
    screen = context.screen
    area = next((a for a in screen.areas if a.type == 'DOPESHEET_EDITOR'), None)

    switched = False
    old_type = None
    old_mode = None

    if area is None:
        candidates = [a for a in screen.areas if a != context.area]
        area = candidates[0] if candidates else screen.areas[0]
        old_type = area.type
        area.type = 'DOPESHEET_EDITOR'
        switched = True
    else:
        # 已有 Dope Sheet：修改前先记录它原来的 mode
        old_mode = area.spaces.active.mode

    space = area.spaces.active
    try:
        space.mode = 'DOPESHEET'

        region = next((r for r in area.regions if r.type == 'WINDOW'), None)
        if region is None:
            raise RuntimeError('找不到 Dope Sheet 的 WINDOW 区域')
        yield area, region
    finally:
        if switched:
            area.type = old_type
        else:
            space.mode = old_mode


class CopyKeyframesOperator(MotionBaseOperator, bpy.types.Operator):
    bl_idname = "mmd_motion_tools.copy_keyframes"
    bl_label = "复制关键帧"
    bl_description = "复制指定范围内的关键帧到目标范围"
    bl_options = {'UNDO'}

    start: bpy.props.IntProperty()
    end: bpy.props.IntProperty()
    repeat_start: bpy.props.IntProperty()
    repeat_end: bpy.props.IntProperty()

    @staticmethod
    def _bone_fcurves(action, bones):
        """返回属于指定骨骼的所有 fcurve"""
        paths = tuple(f'pose.bones["{pb.name}"]' for pb in bones)
        return [fc for fc in action.fcurves if fc.data_path.startswith(paths)]

    @staticmethod
    def _select_keyframes(fcurves, start=None, end=None):
        """
        选中 [start, end] 范围内的关键帧，其余取消选中。
        start/end 为 None 时取消全部选中。
        返回范围内是否存在关键帧。
        """
        found = False
        for fc in fcurves:
            for kp in fc.keyframe_points:
                sel = (
                        start is not None
                        and end is not None
                        and start <= kp.co[0] <= end
                )
                found = found or sel
                set_keyframe_selected(kp, sel)
        return found

    def execute(self, context):
        result = self.check_basic(context)
        if not result:
            return {'CANCELLED'}
        armature, bones = result

        scene = context.scene
        fcurves = self._bone_fcurves(armature.animation_data.action, bones)

        # 选中源范围的关键帧
        if not self._select_keyframes(fcurves, self.start, self.end):
            self.report({'ERROR'}, f'当前选中的骨骼在 {self.start} ~ {self.end} 范围内无关键帧')
            return {'CANCELLED'}

        # 在 Dope Sheet 中复制粘贴（没有则临时切换，结束后恢复）
        old_frame = scene.frame_current
        try:
            with dopesheet_override(context) as (area, region):
                with context.temp_override(area=area, region=region):
                    bpy.ops.action.copy()
                    scene.frame_current = self.repeat_start
                    bpy.ops.action.paste()
        except RuntimeError as e:
            self.report({'ERROR'}, str(e))
            return {'CANCELLED'}
        finally:
            # 恢复当前帧
            scene.frame_current = old_frame
            # 清除选中状态
            self._select_keyframes(fcurves)

        self.report({'INFO'}, f'已复制关键帧：{self.start}-{self.end} → {self.repeat_start}-{self.repeat_end}')
        return {'FINISHED'}
