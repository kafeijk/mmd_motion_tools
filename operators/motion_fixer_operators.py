from ..utils import *


class MotionBaseOperator:
    """动作工具基础类"""

    def check_basic(self, context, count=None):
        armature = self.get_armature(context)
        if not armature:
            return None

        bones = get_selected_bones(armature)

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


class MotionFixerOperator(MotionBaseOperator, bpy.types.Operator):
    bl_idname = "mmd_motion_tools.fix_motion"
    bl_label = "应用修改"
    bl_description = "计算活动骨骼当前姿态与关键帧姿态的差值，并将该变化应用到选中的关键帧上"
    bl_options = {'REGISTER', 'UNDO'}

    margin: bpy.props.IntProperty(
        name="边距",
        description="边缘关键帧范围",
        default=3,
        min=0,
    )

    def invoke(self, context, event):
        self.margin = context.scene.mmd_motion_tools_fix_motion.margin
        return self.execute(context)

    def execute(self, context):
        context.scene.mmd_motion_tools_fix_motion.margin = self.margin
        result = self.check_basic(context, 1)
        if not result:
            return {'CANCELLED'}

        if not self.fix_motion(context, result):
            return {'CANCELLED'}

        return {'FINISHED'}

    def fix_motion(self, context, result):
        # 获取骨骼信息
        armature, bones = result
        pb = bones[0]
        action = armature.animation_data.action

        # 获取影响范围
        frames = get_selected_frames(action, f'pose.bones["{pb.name}"]')
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
            apply_transform(armature, f'pose.bones["{pb.name}"].rotation_quaternion', offset, frame_start, frame_end,
                            current_q, margin = self.margin)

        # 获取骨骼关键帧位置
        old_loc = get_location(armature, f'pose.bones["{pb.name}"].location', current_frame)
        if old_loc:
            # 获取当前骨骼位置
            current_loc = pb.location.copy()
            # 获取位置偏移量
            offset = current_loc - old_loc
            # 应用位置变化
            apply_transform(armature, f'pose.bones["{pb.name}"].location', offset, frame_start, frame_end, current_loc, margin=self.margin)

        return True


class RemoveMarginKeyFrameOperator(MotionBaseOperator, bpy.types.Operator):
    bl_idname = "mmd_motion_tools.remove_margin_keyframe"
    bl_label = "删除边缘帧"
    bl_description = "删除选中关键帧前后指定边距范围内的关键帧"
    bl_options = {'REGISTER', 'UNDO'}

    margin: bpy.props.IntProperty(
        name="边距",
        description="边缘关键帧范围",
        default=3,
        min=0,
    )

    def invoke(self, context, event):
        self.margin = context.scene.mmd_motion_tools_fix_motion.margin
        return self.execute(context)

    def execute(self, context):
        context.scene.mmd_motion_tools_fix_motion.margin = self.margin
        result = self.check_basic(context)
        if not result:
            return {'CANCELLED'}

        if not self.remove_margin_keys(result):
            return {'CANCELLED'}

        return {'FINISHED'}

    def remove_margin_keys(self, result):
        # 获取骨骼信息
        armature, bones = result
        action = armature.animation_data.action

        for pb in bones:
            # 获取影响范围
            frames = get_selected_frames(action, f'pose.bones["{pb.name}"]')
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
    bl_description = "将选中的帧范围从活动骨骼复制到所有选定骨骼"
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
        frames = get_selected_frames(action, f'pose.bones["{active_pb.name}"]')
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
        if len(target_bones) == 1:
            armature.data.bones.active = armature.data.bones[target_bones[0].name]
