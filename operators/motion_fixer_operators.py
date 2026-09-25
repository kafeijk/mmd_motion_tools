from ..utils import *


class MotionBaseOperator:
    """动作工具基础类"""

    def check_basic(self, context, count=None):
        armature = self.get_armature(context)
        if not armature:
            return None

        bones = self.get_selected_bones(armature)

        if not bones:
            self.report({'ERROR'}, '请选择骨骼!')
            return None

        if count and len(bones) != count:
            self.report({'ERROR'}, f'请选择{count}个骨骼!')
            return None

        animation_data = armature.animation_data
        if not animation_data:
            self.report({'ERROR'}, '未检测到关键帧!')
            return None

        action = animation_data.action
        if not action:
            self.report({'ERROR'}, '未检测到关键帧!')
            return None

        return armature, bones

    def get_armature(self, context):
        obj = context.active_object

        if not obj or obj.type != 'ARMATURE':
            self.report({'ERROR'}, '请选择骨架对象!')
            return None

        if context.mode != 'POSE':
            self.report({'ERROR'}, '请进入姿态模式!')
            return None

        return obj

    def get_selected_bones(self, armature):
        return [pb for pb in armature.pose.bones if is_pose_bone_selected(pb)]

    def get_selected_frames(self, action, bone_name):
        """获取指定骨骼选中的关键帧范围"""

        frames = set()
        path = f'pose.bones["{bone_name}"]'

        for fc in action.fcurves:
            if not fc.data_path.startswith(path):
                continue

            for kp in fc.keyframe_points:
                if kp.select_control_point or kp.select_left_handle or kp.select_right_handle:
                    frames.add(int(kp.co[0]))

        # 无数据
        if not frames:
            return None

        frames = sorted(frames)

        start = frames[0]
        end = frames[-1]

        # 多个范围
        if len(frames) != end - start + 1:
            return set()

        return frames


class MotionFixerOperator(MotionBaseOperator, bpy.types.Operator):
    bl_idname = "mmd_motion_tools.fix_motion"
    bl_label = "应用修改"
    bl_description = "应用修改"
    bl_options = {'REGISTER', 'UNDO'}

    margin: bpy.props.IntProperty(
        name="边距",
        description="设置边距",
        default=3,
        min=0,
    )

    type: bpy.props.StringProperty(
        default="FIX",
        options={'HIDDEN'},
    )

    def execute(self, context):
        # TODO 支持MMR或特定骨骼
        # TODO 5.x适配
        # TODO 翻转姿态
        # TODO MMD是否开启的校验
        result = self.check_basic(context, 1 if self.type == "FIX" else None)
        if not result:
            return {'CANCELLED'}
        if self.type == "REMOVE":
            if not self.remove_margin_keys(result):
                return {'CANCELLED'}
        else:
            if not self.fix_motion(context, result):
                return {'CANCELLED'}

        return {'FINISHED'}

    def fix_motion(self, context, result):
        # 获取骨骼信息
        armature, bones = result
        pb = bones[0]
        action = armature.animation_data.action

        # 获取影响范围
        frames = self.get_selected_frames(action, pb.name)
        if frames is None:
            self.report({'ERROR'}, '请选择关键帧!')
            return False
        if len(frames) == 0:
            self.report({'ERROR'}, '请选择连续的关键帧!')
            return False
        frame_start = min(frames)
        frame_end = max(frames)

        # 获取骨骼关键帧旋转值
        current_frame = context.scene.frame_current

        if current_frame < frame_start or current_frame > frame_end:
            self.report({'ERROR'}, '当前帧不在所选关键帧范围内!')
            return False

        old_q = get_quaternion(armature, pb.name, current_frame)
        if old_q:
            # 获取骨骼当前旋转值
            current_q = pb.rotation_quaternion.copy()
            # 获取旋转值偏移量
            offset = calculate_quaternion_offset(old_q, current_q)
            # 应用旋转变化
            apply_transform(armature, pb.name, "rotation_quaternion", offset, frame_start, frame_end, current_q,
                            self.margin)

        # 获取骨骼关键帧位置
        old_loc = get_location(armature, pb.name, current_frame)
        if old_loc:
            # 获取当前骨骼位置
            current_loc = pb.location.copy()
            # 获取位置偏移量
            offset = current_loc - old_loc
            # 应用位置变化
            apply_transform(armature, pb.name, "location", offset, frame_start, frame_end, current_loc, self.margin)

        return True

    def remove_margin_keys(self, result):
        # 获取骨骼信息
        armature, bones = result
        action = armature.animation_data.action

        for pb in bones:
            # 获取影响范围
            frames = self.get_selected_frames(action, pb.name)
            if frames is None:
                self.report({'ERROR'}, '请选择关键帧!')
                return False
            if len(frames) == 0:
                self.report({'ERROR'}, '请选择连续的关键帧!')
                return False

            frame_start = min(frames)
            frame_end = max(frames)

            # 获取骨骼关键帧旋转值
            current_frame = bpy.context.scene.frame_current

            if current_frame < frame_start or current_frame > frame_end:
                self.report({'ERROR'}, '当前帧不在所选关键帧范围内!')
                return False

            # 删除范围边缘的关键帧
            path = f'pose.bones["{pb.name}"]'
            # 避免key为array_index导致data_path被覆盖
            fcurve_map = {
                (fc.data_path, fc.array_index): fc
                for fc in action.fcurves
                if fc.data_path.startswith(path)
            }

            if fcurve_map:
                remove_margin_keys(fcurve_map.values(), frame_start, frame_end, self.margin)

        return True


class CopyRangeOperator(MotionBaseOperator, bpy.types.Operator):
    bl_idname = "mmd_motion_tools.copy_range"
    bl_label = "复制帧范围到选定项"
    bl_description = "激活的骨骼会将帧范围传递至其他选中的骨骼"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        self.copy_range(context)
        return {'FINISHED'}

    def copy_range(self, context):
        result = self.check_basic(context)
        if not result:
            return

        # 获取骨骼信息
        armature, bones = result
        active_pb = armature.pose.bones.get(armature.data.bones.active.name) if armature.data.bones.active else None
        if not active_pb:
            self.report({'ERROR'}, '未检测到激活骨骼!')
            return

        target_bones = [pb for pb in bones if pb != active_pb]
        if not target_bones:
            self.report({'ERROR'}, '未检测到目标骨骼!')
            return

        # 获取影响范围
        action = armature.animation_data.action
        frames = self.get_selected_frames(action, active_pb.name)
        if frames is None:
            self.report({'ERROR'}, '请选择关键帧!')
            return False
        if len(frames) == 0:
            self.report({'ERROR'}, '请选择连续的关键帧!')
            return False

        # 设置目标骨骼的关键帧选择状态
        for target_pb in target_bones:
            path = f'pose.bones["{target_pb.name}"]'
            for fc in action.fcurves:
                if not fc.data_path.startswith(path):
                    continue

                for kp in fc.keyframe_points:
                    selected = int(kp.co[0]) in frames
                    # 若想取消，下面三个参数同时取消才生效
                    kp.select_control_point = selected
                    kp.select_left_handle = selected
                    kp.select_right_handle = selected

        # 取消源骨骼的选中状态，仅保留目标骨骼
        select_pose_bone(active_pb, False)


def apply_transform(armature, bone_name, data_path_type, offset, frame_start, frame_end, current_value, margin=0):
    """应用骨骼的变换修改"""
    action = armature.animation_data.action
    if not action:
        return

    path = f'pose.bones["{bone_name}"].{data_path_type}'
    fcurve_map = {fc.array_index: fc for fc in action.fcurves if fc.data_path == path}
    if not fcurve_map:
        return

    # 获取通道数（维度）
    channels = len(current_value)
    frame_keys = {}

    for index, fc in fcurve_map.items():
        for kp in fc.keyframe_points:
            frame = int(kp.co[0])
            if frame_start <= frame <= frame_end:
                if frame not in frame_keys:
                    frame_keys[frame] = [None] * channels
                frame_keys[frame][index] = kp

    for kps in frame_keys.values():
        # 若缺失通道，则使用当前骨骼值
        values = list(current_value)
        for index, kp in enumerate(kps):
            if kp:
                values[index] = kp.co[1]

        if data_path_type == "rotation_quaternion":
            value = Quaternion(values)
            value = offset @ value
            value.normalize()
        elif data_path_type == "location":
            value = Vector(values) + offset
        else:
            continue

        # 只修改含关键帧的通道
        for index, kp in enumerate(kps):
            if kp:
                kp.co[1] = value[index]

    for fc in fcurve_map.values():
        fc.update()

    if margin > 0:
        remove_margin_keys(fcurve_map.values(), frame_start, frame_end, margin)


def remove_margin_keys(fcurves, frame_start, frame_end, margin):
    """删除范围边缘的关键帧"""
    for fc in fcurves:
        fc.update()

    # 当前曲线最小关键帧
    min_frame = int(min(kp.co[0] for kp in fc.keyframe_points))

    for fc in fcurves:
        for i in range(len(fc.keyframe_points) - 1, -1, -1):
            kp = fc.keyframe_points[i]
            frame = int(kp.co[0])
            if frame == min_frame:
                continue
            if frame_start - margin <= frame < frame_start or frame_end < frame <= frame_end + margin:
                fc.keyframe_points.remove(kp)

    for fc in fcurves:
        fc.update()

    # 差值方式改为平滑
    for fc in fcurves:
        smooth_key_transition(
            fc,
            [
                (frame_start - margin, frame_start),
                (frame_end, frame_end + margin),
            ]
        )

    # 刷新场景以更新视图
    bpy.context.scene.frame_current = bpy.context.scene.frame_current


def find_adjacent_keys(fc, left_frame, right_frame):
    """查找指定范围两侧相邻关键帧"""
    left_key = None
    right_key = None

    for kp in fc.keyframe_points:
        frame = int(kp.co[0])

        if frame <= left_frame:
            left_key = kp

        if frame >= right_frame:
            right_key = kp
            break

    return left_key, right_key


def smooth_key_transition(fc, frame_pairs):
    """设置指定关键帧之间为线性过渡"""
    for left_frame, right_frame in frame_pairs:
        left_key, _ = find_adjacent_keys(fc, left_frame, right_frame)
        if left_key:
            left_key.interpolation = 'LINEAR'

    fc.update()
