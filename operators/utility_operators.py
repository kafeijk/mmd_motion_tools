import traceback
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
        return context.active_object is not None

    def check_props(self, context):
        if not is_mmd_tools_enabled():
            self.report({'ERROR'}, "未启用mmd_tools插件！")
            return False

        obj = context.active_object
        root = find_pmx_root_with_child(obj)
        if root is None:
            self.report({'ERROR'}, "请选择MMD模型!")
            return False

        armature = find_pmx_armature(root)
        if armature is None:
            self.report({'ERROR'}, "未检测到骨架！")
            return False

        physical_bones = get_physical_bone(root)
        if not physical_bones:
            self.report({'ERROR'}, "当前模型不存在受物理影响骨骼!")
            return False

        return True

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "scale")
        layout.prop(self, "margin")

    def execute(self, context):
        start_time = time.time()
        flag = self.check_props(context)
        if not flag:
            return {'CANCELLED'}

        # 获取源对象
        obj = context.active_object
        root = find_pmx_root_with_child(obj)
        armature = find_pmx_armature(root)

        physical_bones = set(get_physical_bone(root))

        # 物理关键帧直接写入当前骨架的 Action
        success, message = import_vmd_physics(
            armature,
            filepath=bpy.path.abspath(self.filepath),
            physical_bones=physical_bones,
            scale=self.scale,
            margin=self.margin,
        )

        if not success:
            self.report({'ERROR'}, message)
            return {'CANCELLED'}

        # 通过物理骨骼的 IK 约束查找对应的 IK，并清除 IK 动画、关闭 IK
        set_physical_bone_ik(armature, physical_bones)

        armature.update_tag()
        context.scene.frame_set(context.scene.frame_current)

        self.report({'INFO'}, f"{message}执行耗时: {time.time() - start_time:.2f} 秒")
        return {'FINISHED'}


class PhysicalBoneOperatorBase:
    """物理骨骼动画工具基础类，统一处理 PMX 定位与前置校验"""

    @classmethod
    def poll(cls, context):
        return context.active_object is not None

    def check_props(self, context, need_action=False):
        """校验前置条件，返回 (armature, bone_names) 或 None"""
        if not is_mmd_tools_enabled():
            self.report({'ERROR'}, "未启用mmd_tools插件！")
            return None

        obj = context.active_object

        root = find_pmx_root_with_child(obj)
        if root is None:
            self.report({'ERROR'}, "请选择MMD模型！")
            return None

        armature = find_pmx_armature(root)
        if armature is None:
            self.report({'ERROR'}, "未检测到骨架！")
            return None

        physical_bones = get_physical_bone(root)
        if not physical_bones:
            self.report({'ERROR'}, "当前模型不存在受物理影响骨骼!")
            return None

        if need_action and (not armature.animation_data or not armature.animation_data.action):
            self.report({'ERROR'}, "当前骨架不存在动画!")
            return None

        return armature, set(physical_bones)


class RemovePhysicalBoneAnimationOperator(PhysicalBoneOperatorBase, bpy.types.Operator):
    bl_idname = "mmd_motion_tools.remove_physical_bone_animation"
    bl_label = "移除物理"
    bl_description = "移除当前模型物理骨骼的动画关键帧"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        result = self.check_props(context, need_action=True)
        if not result:
            return {'CANCELLED'}

        flag = self.remove_physical_bone_animation(context)

        if not flag:
            return {'CANCELLED'}

        return {'FINISHED'}

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
        for fc in get_action_fcurves(action):
            data_path = fc.data_path

            # 只处理骨骼动画
            if not data_path.startswith(BONE_PATH_PREFIX):
                continue

            # 获取骨骼名称
            bone_name = data_path.split('"', 2)[1]

            if bone_name in physical_bones:
                remove_fcurves.append(fc)
                remove_bone_names.add(bone_name)

        remove_action_fcurves(action, remove_fcurves)

        set_physical_bone_ik(armature, physical_bones, enabled=True)

        pose_bones = armature.pose.bones
        for name in physical_bones:
            pose_bone = pose_bones.get(name)
            if pose_bone is not None:
                clear_pose_transform(pose_bone)

        # 只标记更新，不调用 view_layer.update()：
        armature.update_tag()

        self.report({'INFO'}, f"已移除 {len(remove_bone_names)} 个物理骨骼的动画")

        return True


def get_physical_bone_fcurves(armature, bone_names):
    """返回当前 Action 中属于指定骨骼的 F-Curve 列表。bone_names 为 set，成员判断为 O(1)。"""
    animation_data = getattr(armature, 'animation_data', None)
    action = animation_data.action if animation_data else None

    return get_action_bone_fcurves(action, bone_names)


def clear_pose_transform(pose_bone):
    """将单个骨骼的变换置为默认值"""
    pose_bone.location = (0.0, 0.0, 0.0)
    pose_bone.scale = (1.0, 1.0, 1.0)

    if pose_bone.rotation_mode == 'QUATERNION':
        pose_bone.rotation_quaternion = (1.0, 0.0, 0.0, 0.0)
    else:
        pose_bone.rotation_euler = (0.0, 0.0, 0.0)


class MutePhysicalBoneAnimationOperator(PhysicalBoneOperatorBase, bpy.types.Operator):
    bl_idname = "mmd_motion_tools.mute_physical_bone_animation"
    bl_label = "禁用物理"
    bl_description = "使物理骨骼的动画失效"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        result = self.check_props(context, need_action=True)
        if not result:
            return {'CANCELLED'}

        armature, bone_names = result
        fcurves = get_physical_bone_fcurves(armature, bone_names)

        if not fcurves:
            self.report({'ERROR'}, "物理骨骼没有动画曲线!")
            return {'CANCELLED'}

        # 使通道静音
        for fc in fcurves:
            fc.mute = True

        pose_bones = armature.pose.bones
        for name in bone_names:
            pose_bone = pose_bones.get(name)
            if pose_bone is not None:
                clear_pose_transform(pose_bone)

        # 只标记更新，不调用 view_layer.update()：
        armature.update_tag()
        return {'FINISHED'}


class UnmutePhysicalBoneAnimationOperator(PhysicalBoneOperatorBase, bpy.types.Operator):
    bl_idname = "mmd_motion_tools.unmute_physical_bone_animation"
    bl_label = "恢复物理"
    bl_description = "恢复物理骨骼的动画"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        result = self.check_props(context)
        if not result:
            return {'CANCELLED'}

        armature, bone_names = result

        for fc in get_physical_bone_fcurves(armature, bone_names):
            if not fc.mute:
                continue
            fc.mute = False

        armature.update_tag()
        return {'FINISHED'}


def get_vmd_bones(filepath):
    """读取 VMD 文件，获取其中包含关键帧的骨骼名称，失败返回 None"""
    modules = load_mmd_tools_modules()
    if modules is None:
        return None

    try:
        vmd_file = modules.vmd.File()
        vmd_file.load(filepath=filepath)
    except Exception:
        traceback.print_exc()
        return None

    return list(vmd_file.boneAnimation.keys())


@timeit
def import_vmd_physics(armature, filepath, physical_bones, scale=0.08, margin=0):
    """
    把 VMD 中物理骨骼的关键帧直接写入当前活动骨架的 Action。
    返回 (是否成功, 提示信息)：失败时提示信息可直接 report 给用户。
    """
    modules = load_mmd_tools_modules()
    if modules is None:
        return False, "无法加载MMD Tools的VMD模块，请检查当前MMD Tools版本是否兼容！"

    # 构造 VMDImporter：这一步会完整解析整个 VMD 文件
    try:
        importer = modules.new_importer(filepath, scale=scale, frame_margin=margin)
    except Exception:
        return False, "VMD文件读取失败，请检查文件是否有效！"

    # 获取解析完成的 vmd_file 实例
    vmd_file = modules.get_parsed_file(importer)
    if vmd_file is None:
        return False, "VMD解析结果获取失败，请检查当前MMD Tools版本是否兼容！"

    # 获取物理骨骼日文名称
    bl_jp_map = get_bl_jp_map(armature, modules.makePmxBoneMap)
    physical_jp_bl_map = {}
    for bl_name in physical_bones:
        name_j = bl_jp_map.get(bl_name)
        if name_j:
            physical_jp_bl_map.setdefault(name_j, bl_name)

    # 只保留物理骨骼的关键帧
    bone_animation = modules.vmd.BoneAnimation()
    for jp_name, frames in vmd_file.boneAnimation.items():
        if jp_name in physical_jp_bl_map:
            bone_animation[jp_name] = frames

    # 真正会被写入的blender骨骼名，用于后续覆盖/合并
    written_bones = {physical_jp_bl_map[jp_name] for jp_name in bone_animation}

    # 校验物理骨骼关键帧是否存在
    bone_count = len(bone_animation)
    frame_count = sum(len(frames) for frames in bone_animation.values())
    if bone_count < 1 or frame_count < 1:
        return False, "vmd文件中不存在物理骨骼关键帧!请使用新版MMD桥烘焙得到的文件进行导入"

    # 回写裁剪后的骨骼动画
    vmd_file.boneAnimation = bone_animation

    # 物理导入不需要 IK 开关动画（后续统一由 set_physical_bone_ik 关闭物理相关 IK）
    vmd_file.propertyAnimation = modules.vmd.PropertyAnimation()

    # 准备目标 Action，没有动画数据时先建一个
    dst_action = ensure_action(armature, action_name=f"{armature.name}_Action")
    dst_name = dst_action.name

    # 移除已有关键帧
    remove_bone_fcurves(dst_action, written_bones)

    # 交由 mmd_tools 生成物理骨骼的 F-Curve，两个分支：
    #   4.2+ ：importer 复用 dst_action 并直接写入，到这里就已经完成写入
    #   3.6  ：importer 新建一个临时 Action 挂到骨架上，之后需要合并回 dst_action
    action_name = f"{os.path.splitext(os.path.basename(filepath))[0]}_physics"
    importer.assign(armature, action_name)

    animation_data = armature.animation_data
    produced_action = animation_data.action

    try:
        # 用名称比较而不是 is：RNA 包装对象可能不是同一个 Python 实例，
        # 而 bpy.data.actions 内名称唯一，因此按名称判断与判断身份等价且更稳。
        if produced_action is not None and produced_action.name != dst_name:
            try:
                # 合并物理数据到目标 Action
                copy_bone_fcurves(produced_action, dst_action, written_bones)
            finally:
                # 临时 Action 用完即删，并把骨架原本的 Action 换回来
                animation_data.action = dst_action
                remove_action(produced_action)
    except Exception:
        return False, "物理数据写入失败，请检查当前MMD Tools版本是否兼容！"

    # Blender 5.0 起动画数据必须绑定到 action_slot，这里统一补一次
    assign_action(armature, dst_action)

    return True, f"已导入 {len(written_bones)} 个物理骨骼的关键帧。"


def remove_action(action):
    """删除无用 Action"""
    if action is None:
        return

    try:
        if action.name in bpy.data.actions:
            bpy.data.actions.remove(action)
    except (ReferenceError, RuntimeError):
        pass


@timeit
def copy_bone_fcurves(src_action, dst_action, bone_names):
    """将指定骨骼的动画从源 Action 完整覆盖到目标 Action。"""

    if not src_action:
        return

    if not dst_action:
        return

    bone_names = set(bone_names)

    # 缓存目标 F-Curve
    dst_fcurves = {
        (fc.data_path, fc.array_index): fc
        for fc in get_action_fcurves(dst_action)
    }

    for fc in get_action_fcurves(src_action):
        data_path = fc.data_path

        # 只处理骨骼动画
        if not data_path.startswith(BONE_PATH_PREFIX):
            continue

        # 获取骨骼名称
        bone_name = data_path.split('"', 2)[1]

        if bone_name not in bone_names:
            continue

        key = (data_path, fc.array_index)
        new_fc = dst_fcurves.get(key)

        if new_fc is None:
            new_fc = new_action_fcurve(
                dst_action,
                data_path=data_path,
                array_index=fc.array_index,
            )

            if new_fc is None:
                continue

            dst_fcurves[key] = new_fc

            # 沿用源曲线的骨骼分组，合并后不至于丢失 Action 里的分组结构
            set_fcurve_group(dst_action, new_fc, getattr(fc, 'group', None))
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

        for fc in get_action_fcurves(action):
            if fc.data_path in data_paths:
                remove_action_fcurves(action, [fc])

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

            for fc in get_action_fcurves(action):
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
        for fc in get_action_fcurves(action):
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

            # 避免浮点误差
            if x != 0 and y != 0 and abs(x - y) < 1e-6:
                return True

            if abs(x - y) / abs(x) > (self.error / 100):
                return False

        return True

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

        # 两段重复的动作间，repeat_start - start必须大于最小连续重复帧数
        # 如果是循环类动作，应调整threshold来从小范围向大范围寻找，而不是大范围内还包含小范围
        threshold = self.threshold
        filtered = [
            (x["start"], x["end"], x["repeat_start"], x["repeat_end"])
            for x in filtered
            if x["repeat_start"] - x["start"] > threshold
        ]
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


class SyncKeyframesOperator(MotionBaseOperator, bpy.types.Operator):
    bl_idname = "mmd_motion_tools.sync_keyframes"
    bl_label = "同步关键帧"
    bl_description = "将选中骨骼在指定范围内的关键帧同步到目标范围"
    bl_options = {'UNDO'}

    # 覆盖基类的默认提示，让同步关键帧的报错更有指向性
    select_bones_message = '请选择需要同步的骨骼'

    start: bpy.props.IntProperty()
    end: bpy.props.IntProperty()
    repeat_start: bpy.props.IntProperty()
    repeat_end: bpy.props.IntProperty()

    @staticmethod
    def _bone_fcurves(action, bones):
        """返回属于指定骨骼的所有 fcurve"""
        paths = tuple(f'pose.bones["{pb.name}"]' for pb in bones)
        return [fc for fc in get_action_fcurves(action) if fc.data_path.startswith(paths)]

    @staticmethod
    def _remove_range_keyframes(fcurves, start, end, exclude_start=None, exclude_end=None):
        """
        删除 [start, end] 范围内的关键帧，返回被删除的数量。

        exclude_start / exclude_end 给定一段保护区间，落在其中的关键帧不删除：
        源范围与目标范围可能重叠，若不排除会把待复制的关键帧一起删掉。
        """
        removed = 0
        protected = exclude_start is not None and exclude_end is not None

        for fc in fcurves:
            keyframe_points = fc.keyframe_points

            doomed = [
                i for i, frame in enumerate(get_fcurve_frames(fc))
                if start <= frame <= end
                   and not (protected and exclude_start <= frame <= exclude_end)
            ]

            if not doomed:
                continue

            for i in reversed(doomed):
                keyframe_points.remove(keyframe_points[i])

            removed += len(doomed)
            fc.update()

        return removed

    @staticmethod
    def _select_keyframes(fcurves, start=None, end=None):
        """
        选中 [start, end] 范围内的关键帧，其余取消选中。
        start/end 为 None 时取消全部选中。
        返回范围内是否存在关键帧。
        """
        found = False
        for fc in fcurves:
            if start is not None and end is not None:
                count = select_fcurve_keyframes_in_range(fc, start, end)
            else:
                set_fcurve_keyframes_selected(fc, False)
                count = 0

            found = found or count > 0

        return found

    def execute(self, context):
        result = self.check_basic(context)
        if not result:
            return {'CANCELLED'}
        armature, bones = result

        scene = context.scene
        fcurves = self._bone_fcurves(armature.animation_data.action, bones)

        # 清除目标范围内的旧关键帧，避免粘贴后残留多余的帧
        # 与源范围重叠的部分保留，否则会把待复制的关键帧一起删掉
        self._remove_range_keyframes(fcurves, self.repeat_start, self.repeat_end,
                                     exclude_start=self.start, exclude_end=self.end)

        # 选中源范围的关键帧
        self._select_keyframes(fcurves, self.start, self.end)

        # 在 Dope Sheet 中复制粘贴（没有则临时切换，结束后恢复）
        old_frame = scene.frame_current
        try:
            with dopesheet_override(context) as (area, region):
                with context.temp_override(area=area, region=region):
                    bpy.ops.action.copy()
                    scene.frame_current = self.repeat_start
                    bpy.ops.action.paste()
        except RuntimeError:
            # 指定范围无关键帧则pass
            pass
        finally:
            # 恢复当前帧
            scene.frame_current = old_frame
            # 清除选中状态
            self._select_keyframes(fcurves)

        self.report({'INFO'}, f'已同步关键帧：{self.start}-{self.end} → {self.repeat_start}-{self.repeat_end}')
        return {'FINISHED'}
