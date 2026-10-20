from ..utils import *


class MotionBaseOperator:
    """动作工具基础类"""

    select_bones_message = '请选择骨骼!'

    def check_basic(self, context, check_select=True, count=None):
        armature = self.get_armature(context)
        if not armature:
            return None

        bones = get_selected_bones(armature)

        if check_select and not bones:
            self.report({'ERROR'}, self.select_bones_message)
            return None

        if check_select and count and len(bones) != count:
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

    def check_multi(self, context):
        result = self.check_basic(context)
        if not result:
            return None

        armature, bones = result
        action = armature.animation_data.action
        current_frame = context.scene.frame_current

        fcurves = get_action_fcurves(action)

        frame_start = None
        frame_end = None

        pairs = []
        for pb in bones:
            frames = get_selected_frames(action, f'pose.bones["{pb.name}"]', fcurves=fcurves)

            if frames is None:
                if len(bones) == 1:
                    self.report({'ERROR'}, f'请选择 {pb.name} 的关键帧!')
                    return None
                continue

            # 无论选中多少骨骼，都不允许单个骨骼存在多个关键帧范围
            if len(frames) == 0:
                self.report({'ERROR'}, f'请选择 {pb.name} 连续的关键帧!')
                return None

            current_start = min(frames)
            current_end = max(frames)

            if frame_start is None:
                frame_start = current_start
                frame_end = current_end
            else:
                frame_start = min(frame_start, current_start)
                frame_end = max(frame_end, current_end)
            pairs.append((current_start, current_end))

        if frame_start is None:
            self.report({'ERROR'}, f'请选择关键帧!')
            return None

        # 当前帧至少在某个关键帧范围内
        if pairs and not any(cs <= current_frame <= ce for cs, ce in pairs):
            self.report({'ERROR'}, '当前帧不在所选关键帧范围内！')
            return None

        # 关键帧范围之间必须是相交的
        if pairs:
            max_pair = max(pairs, key=lambda pair: pair[1] - pair[0])

            for cs, ce in pairs:
                if ce < max_pair[0] or cs > max_pair[1]:
                    self.report({'ERROR'}, f'请同步选择范围后重试!')
                    return None

        if current_frame < frame_start or current_frame > frame_end:
            self.report({'ERROR'}, '当前帧不在所选关键帧范围内!')
            return None

        return armature, bones, frame_start, frame_end

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
        description="外侧关键帧范围",
        default=3,
        min=0,
    )

    # ensure_current 在 Blender 5.0 中表现为虽然已注册，但获取到的值仍为调整前的值；Blender 4.5 和 5.2 中均正常。
    ensure_current: bpy.props.BoolProperty(
        name="注册当前帧",
        description="注册当前关键帧，保持修改前后姿势一致。\n当选中多个骨骼且当前帧在所选帧的边界上时，可能会因为缺帧导致修改前后姿势不一致，开启此选项可自动注册关键帧，保持姿势一致。",
        default=False,
    )

    def invoke(self, context, event):
        self.margin = context.scene.mmd_motion_tools_fix_motion.margin
        return self.execute(context)

    def execute(self, context):
        context.scene.mmd_motion_tools_fix_motion.margin = self.margin
        result = self.check_multi(context)
        if not result:
            return {'CANCELLED'}

        if not self.fix_motion(context, result):
            return {'CANCELLED'}

        return {'FINISHED'}

    def fix_motion(self, context, result):
        armature, bones, frame_start, frame_end = result
        action = armature.animation_data.action
        current_frame = context.scene.frame_current

        fcurves = get_action_fcurves(action) if self.ensure_current else []
        for pb in bones:
            # 确保当前帧存在关键帧
            if self.ensure_current:
                path = f'pose.bones["{pb.name}"]'
                fcurve_map = {
                    (fc.data_path, fc.array_index): fc
                    for fc in fcurves
                    if fc.data_path.startswith(path)
                }

                if not fcurve_map:
                    continue

                ensure_keyframes(fcurve_map.values(), current_frame, current_frame)

            old_q = get_quaternion(armature, pb.name, current_frame)
            if old_q:
                # 获取骨骼当前旋转值
                current_q = pb.rotation_quaternion.copy()
                # 获取旋转值偏移量
                offset = calculate_quaternion_offset(old_q, current_q)
                # 应用旋转变化
                apply_transform(armature, f'pose.bones["{pb.name}"].rotation_quaternion', offset, frame_start,
                                frame_end, current_q, margin=self.margin)

            # 获取骨骼关键帧位置
            old_loc = get_location(armature, f'pose.bones["{pb.name}"].location', current_frame)
            if old_loc:
                # 获取当前骨骼位置
                current_loc = pb.location.copy()
                # 获取位置偏移量
                offset = current_loc - old_loc
                # 应用位置变化
                apply_transform(armature, f'pose.bones["{pb.name}"].location', offset, frame_start, frame_end,
                                current_loc, margin=self.margin)

        return True


class RemoveOuterKeyframesOperator(MotionBaseOperator, bpy.types.Operator):
    bl_idname = "mmd_motion_tools.remove_outer_keyframe"
    bl_label = "删除外侧帧"
    bl_description = "删除选中关键帧外侧指定边距范围内的关键帧"
    bl_options = {'REGISTER', 'UNDO'}

    margin: bpy.props.IntProperty(
        name="边距",
        description="外侧关键帧范围",
        default=3,
        min=0,
    )

    def invoke(self, context, event):
        self.margin = context.scene.mmd_motion_tools_fix_motion.margin
        return self.execute(context)

    def execute(self, context):
        context.scene.mmd_motion_tools_fix_motion.margin = self.margin
        result = self.check_multi(context)
        if not result:
            return {'CANCELLED'}

        if not self.remove_margin_keys(result):
            return {'CANCELLED'}

        return {'FINISHED'}

    def remove_margin_keys(self, result):
        # 获取骨骼信息
        armature, bones, frame_start, frame_end = result
        action = armature.animation_data.action

        fcurves = get_action_fcurves(action)
        for pb in bones:
            # 删除外侧的关键帧
            path = f'pose.bones["{pb.name}"]'
            # 避免key为array_index导致data_path被覆盖
            fcurve_map = {
                (fc.data_path, fc.array_index): fc
                for fc in fcurves
                if fc.data_path.startswith(path)
            }

            if fcurve_map:
                remove_margin_keys(fcurve_map.values(), frame_start, frame_end, self.margin)

        return True


class SyncRangeOperator(MotionBaseOperator, bpy.types.Operator):
    bl_idname = "mmd_motion_tools.sync_range"
    bl_label = "同步范围选择"
    bl_description = "将选中的帧范围从活动骨骼同步到所有选定骨骼"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        result = self.check_basic(context)
        if not result:
            return {'CANCELLED'}

        if not self.sync_range(context, result):
            return {'CANCELLED'}

        return {'FINISHED'}

    def sync_range(self, context, result):
        # 获取骨骼信息
        armature, bones = result
        active_pb = armature.pose.bones.get(armature.data.bones.active.name) if armature.data.bones.active else None
        if not active_pb:
            self.report({'ERROR'}, '未检测到激活骨骼!')
            return False

        target_bones = [pb for pb in bones if pb != active_pb]
        if not target_bones:
            self.report({'ERROR'}, '未检测到目标骨骼!')
            return False

        # 获取影响范围
        action = armature.animation_data.action
        frames = get_selected_frames(action, f'pose.bones["{active_pb.name}"]')
        if frames is None:
            self.report({'ERROR'}, '请选择关键帧!')
            return False
        if len(frames) == 0:
            self.report({'ERROR'}, '请选择连续的关键帧!')
            return False

        frame_start = min(frames)
        frame_end = max(frames)

        # 设置目标骨骼的关键帧选择状态
        paths = tuple(f'pose.bones["{pb.name}"]' for pb in target_bones)

        for fc in get_action_fcurves(action):
            if not fc.data_path.startswith(paths):
                continue

            select_fcurve_keyframes_in_range(fc, frame_start, frame_end)
        return True


class RemoveInnerKeyframesOperator(MotionBaseOperator, bpy.types.Operator):
    bl_idname = "mmd_motion_tools.remove_inner_keyframe"
    bl_label = "删除内侧帧"
    bl_description = "删除选中关键帧内侧的关键帧"
    bl_options = {'REGISTER', 'UNDO'}

    ensure_boundary: bpy.props.BoolProperty(
        name="注册边界帧",
        description="注册边界关键帧，保持边界姿势一致",
        default=True,
    )

    def execute(self, context):
        result = self.check_multi(context)
        if not result:
            return {'CANCELLED'}

        if not self.remove_inner_keys(result):
            return {'CANCELLED'}

        return {'FINISHED'}

    def remove_inner_keys(self, result):
        armature, bones, frame_start, frame_end = result
        action = armature.animation_data.action

        fcurves = get_action_fcurves(action)
        for pb in bones:
            path = f'pose.bones["{pb.name}"]'

            # 避免 key 为 array_index 导致 data_path 被覆盖
            fcurve_map = {
                (fc.data_path, fc.array_index): fc
                for fc in fcurves
                if fc.data_path.startswith(path)
            }

            if not fcurve_map:
                continue

            # 确保范围两侧存在关键帧
            if self.ensure_boundary:
                ensure_keyframes(fcurve_map.values(), frame_start, frame_end)

            # 删除范围内部的关键帧
            remove_inner_keys(fcurve_map.values(), frame_start, frame_end)

        return True


def ensure_keyframes(fcurves, frame_start, frame_end):
    """确保范围两侧存在关键帧，不存在则使用 evaluate 值注册"""
    fcurves = list(fcurves)

    for fc in fcurves:
        frames = {int(kp.co[0]) for kp in fc.keyframe_points}

        if frame_start not in frames:
            value = fc.evaluate(frame_start)
            fc.keyframe_points.insert(frame_start, value)

        if frame_end not in frames:
            value = fc.evaluate(frame_end)
            fc.keyframe_points.insert(frame_end, value)

    for fc in fcurves:
        fc.update()


def remove_inner_keys(fcurves, frame_start, frame_end):
    """删除指定范围内部的关键帧，保留范围两端关键帧"""
    fcurves = list(fcurves)

    # 每条曲线在这个区间里「有没有真的删掉键」：删过的形状已经没意义，拉成直线
    removed_flags = []

    for fc in fcurves:
        removed = False
        for i in range(len(fc.keyframe_points) - 1, -1, -1):
            kp = fc.keyframe_points[i]
            frame = int(kp.co[0])

            if frame_start < frame < frame_end:
                fc.keyframe_points.remove(kp)
                removed = True
        removed_flags.append(removed)

    for fc in fcurves:
        fc.update()

    # 过渡统一成贝塞尔：删过键的拉成共线直线，没删过的保持原有曲线
    for fc, removed in zip(fcurves, removed_flags):
        smooth_key_transition(fc, [(frame_start, frame_end)], straight=removed)

    # 刷新场景以更新视图
    bpy.context.scene.frame_set(bpy.context.scene.frame_current)


class DeselectKeyframesOperator(MotionBaseOperator, bpy.types.Operator):
    bl_idname = "mmd_motion_tools.deselect_keyframes"
    bl_label = "取消范围选择"
    bl_description = "取消所有骨骼的关键帧的选中状态"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        result = self.check_basic(context, check_select=False)
        if not result:
            return {'CANCELLED'}

        armature, _ = result
        action = armature.animation_data.action

        fcurves = get_action_fcurves(action)
        bone_names = {pb.name for pb in armature.pose.bones}

        for fc in fcurves:
            data_path = fc.data_path

            if not data_path.startswith(BONE_PATH_PREFIX):
                continue

            if data_path.split('"', 2)[1] not in bone_names:
                continue

            set_fcurve_keyframes_selected(fc, False)

        # 刷新场景，立即显示结果
        context.scene.frame_set(context.scene.frame_current)
        return {'FINISHED'}
