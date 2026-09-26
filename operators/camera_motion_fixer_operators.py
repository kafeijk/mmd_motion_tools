from ..utils import *


class CameraMotionFixerOperator( bpy.types.Operator):
    bl_idname = "mmd_motion_tools.fix_camera_motion"
    bl_label = "应用相机修改"
    bl_description = "计算MMD相机当前状态与关键帧状态的差值，并将该变化应用到选中的关键帧上"
    bl_options = {'REGISTER', 'UNDO'}

    margin: bpy.props.IntProperty(
        name="边距",
        description="边缘关键帧范围",
        default=0,
        min=0,
    )

    def check_props(self, context):
        obj = context.active_object

        # 校验MMD相机及MMD相机根对象是否存在
        if is_mmd_camera_root(obj):
            mmd_camera = obj
            cameras = find_children(mmd_camera, "CAMERA")
            if len(cameras) != 1 or not is_mmd_camera(cameras[0]):
                self.report({'ERROR'}, '未检测到MMD相机，请检查!')
                return False, None, None
            camera = cameras[0]
        else:
            camera = obj
            mmd_camera = find_mmd_camera_root(camera)
            if not mmd_camera:
                self.report({'ERROR'}, '未检测到MMD相机的根对象，请检查!')
                return False, None, None

        def check_animation(obj, name):
            """校验动画数据是否存在"""
            animation_data = obj.animation_data
            if not animation_data or not animation_data.action:
                self.report({'ERROR'}, f'未检测到{name}关键帧!')
                return False
            return True

        if not check_animation(camera, "MMD相机"):
            return False, None, None

        if not check_animation(mmd_camera, "MMD相机根对象"):
            return False, None, None

        return True, mmd_camera, camera

    def execute(self, context):
        success, mmd_camera, camera = self.check_props(context)
        if not success:
            return {'CANCELLED'}

        if not self.fix_camera_motion(context, mmd_camera, camera):
            return {'CANCELLED'}

        return {'FINISHED'}

    def fix_camera_motion(self, context, mmd_camera, camera):
        loc_fcurves = []
        rot_fcurves = []
        angle_fcurves = []
        distance_fcurves = []
        frames = set()

        def collect_fcurves(action, data_path, target_list, array_index=None):
            """收集指定类型的FCurve并获取选中帧"""
            fs = get_selected_frames(action, data_path, array_index=array_index)
            # 单个 data_path 未选中无需处理，直接返回，统一在后续检查是否存在选中项
            if fs is None:
                return True
            if len(fs) == 0:
                self.report({'ERROR'}, '请选择连续的关键帧!')
                return False

            fcurves = action.fcurves
            for fc in fcurves:
                if fc.data_path != data_path:
                    continue
                if array_index is not None and fc.array_index != array_index:
                    continue
                target_list.append(fc)
            frames.update(fs)
            return True

        action = mmd_camera.animation_data.action
        if not collect_fcurves(action, "location", loc_fcurves):
            return False

        if not collect_fcurves(action, "rotation_euler", rot_fcurves):
            return False

        if not collect_fcurves(action, "mmd_camera.angle", angle_fcurves):
            return False

        camera_action = camera.animation_data.action
        if not collect_fcurves(camera_action, "location", distance_fcurves, array_index=1):
            return False

        if len(frames) == 0:
            self.report({'ERROR'}, '请选择关键帧!')
            return False

        frame_start = min(frames)
        frame_end = max(frames)

        current_frame = context.scene.frame_current
        if current_frame < frame_start or current_frame > frame_end:
            self.report({'ERROR'}, '当前帧不在所选关键帧范围内!')
            return False

        # 针对旋转值的修改
        old_e = get_camera_euler(mmd_camera, "rotation_euler", current_frame)
        if old_e:
            # 获取 mmd_camera 当前旋转值
            current_e = mmd_camera.rotation_euler.copy()
            # 获取旋转值偏移量
            offset = calculate_euler_offset(old_e, current_e)
            # 应用旋转变化
            apply_transform(mmd_camera, "rotation_euler", offset, frame_start, frame_end, current_e, margin = self.margin)

        # 针对 mmd_camera 角度的修改
        old_angle = get_camera_value(mmd_camera, "mmd_camera.angle", current_frame)
        if old_angle is not None:   # 角度可能为0
            # 获取 mmd_camera 当前旋转值
            current_angle = mmd_camera.mmd_camera.angle
            # 获取旋转值偏移量
            offset = current_angle - old_angle
            # 应用旋转变化
            apply_transform(mmd_camera, "mmd_camera.angle", offset, frame_start, frame_end, current_angle,margin = self.margin)

        # 针对 mmd_camera 位置的修改
        old_loc = get_location(mmd_camera, "location", current_frame)
        if old_loc:
            # 获取当前骨骼位置
            current_loc = mmd_camera.location.copy()
            # 获取位置偏移量
            offset = current_loc - old_loc
            # 应用位置变化
            apply_transform(mmd_camera, f'location', offset, frame_start, frame_end, current_loc, margin = self.margin)

        # 针对 camera 距离的修改
        old_dis = get_camera_value(camera, "location", current_frame, 1)
        if old_dis is not None:  # 位置可能为0
            # 获取 camera 当前距离
            current_dis = camera.location[1]
            # 获取旋转值偏移量
            offset = current_dis - old_dis
            # 应用旋转变化
            apply_transform(camera, "location", offset, frame_start, frame_end, current_dis,
                            array_index=1, margin=self.margin)
        return True


class FindCameraSegmentOperator(bpy.types.Operator):
    bl_idname = "mmd_motion_tools.find_camera_segment"
    bl_label = "查找片段"
    bl_description = "查找片段"
    bl_options = {'REGISTER', 'UNDO'}

    clean: bpy.props.BoolProperty(
        name="预清理",
        description="查找片段前先进行清理关键帧。此操作仅用于分析，不会修改原始动画数据。",
        default=False
    )

    def check_props(self, context):
        obj = context.active_object

        # 校验MMD相机及MMD相机根对象是否存在
        if is_mmd_camera_root(obj):
            mmd_camera = obj
            cameras = find_children(mmd_camera, "CAMERA")
            if len(cameras) != 1 or not is_mmd_camera(cameras[0]):
                self.report({'ERROR'}, '未检测到MMD相机，请检查!')
                return False, None, None
            camera = cameras[0]
        else:
            camera = obj
            mmd_camera = find_mmd_camera_root(camera)
            if not mmd_camera:
                self.report({'ERROR'}, '未检测到MMD相机的根对象，请检查!')
                return False, None, None

        def check_animation(obj, name):
            """校验动画数据是否存在"""
            animation_data = obj.animation_data
            if not animation_data or not animation_data.action:
                self.report({'ERROR'}, f'未检测到{name}关键帧!')
                return False
            return True

        if not check_animation(camera, "MMD相机"):
            return False, None, None

        if not check_animation(mmd_camera, "MMD相机根对象"):
            return False, None, None

        return True, mmd_camera, camera

    def execute(self, context):
        success, mmd_camera, camera = self.check_props(context)
        if not success:
            return {'CANCELLED'}

        if not self.find_camera_segment(context, mmd_camera, camera):
            return {'CANCELLED'}

        return {'FINISHED'}

    def find_camera_segment(self, context, mmd_camera, camera):
        """"""
        # 当前帧
        frame_current = context.scene.frame_current

        # 获取筛选后的fcurves
        filtered_fcurves = []
        fcurves = mmd_camera.animation_data.action.fcurves
        for fc in fcurves:
            if fc.data_path not in ["location", "rotation_quaternion", "mmd_camera.angle"]:
                continue
            filtered_fcurves.append(fc)
        fcurves = camera.animation_data.action.fcurves
        for fc in fcurves:
            if fc.data_path != "location":
                continue
            if fc.array_index != 1:
                continue
            filtered_fcurves.append(fc)

        # 逻辑过滤，不影响原始数据
        source_fcurves = filtered_fcurves
        if self.clean:
            filtered_fcurves = copy_fcurves(filtered_fcurves)
            filtered_fcurves = clean_fcurves(filtered_fcurves)

        # 寻找边界对及首尾帧
        boundaries_list = []
        first = float("inf")
        last = float("-inf")
        for fc in filtered_fcurves:
            curr_first, curr_last, boundaries = find_boundaries(fc)

            if boundaries:
                boundaries_list.append(boundaries)
                first = min(first, curr_first)
                last = max(last, curr_last)

        if not boundaries_list:
            first = last = None

        # 获取最窄边界对
        start = first
        end = last
        for boundaries in boundaries_list:
            for left, right in boundaries:
                if left < frame_current:
                    start = max(start, left)
                if frame_current < right:
                    end = min(end, right)

        # 设置目标相机的关键帧选择状态
        if source_fcurves:
            filtered_fcurves = source_fcurves

        for fc in filtered_fcurves:
            for kp in fc.keyframe_points:
                selected = start <= kp.co[0] <= end
                # 若想取消，下面三个参数同时取消才生效
                kp.select_control_point = selected
                kp.select_left_handle = selected
                kp.select_right_handle = selected

        return True


@timeit
def find_boundaries(fcurve):
    """寻找关键帧区域的左右边界"""

    frames = sorted([int(kp.co[0]) for kp in fcurve.keyframe_points])

    if len(frames) < 2:
        return []

    result = []
    start = frames[0]

    for i in range(len(frames) - 1):
        if frames[i + 1] - frames[i] <= 1:
            result.append((start, frames[i]))
            start = frames[i + 1]

    result.append((start, frames[-1]))

    # 移除首尾单帧边界
    first = frames[0]
    last = frames[-1]

    result = [
        (left, right)
        for left, right in result
        if not (left == right and (left == first or right == last))
    ]

    return first, last, result


class FakeFCurve:
    """用于分析的FCurve副本"""

    def __init__(self, fc):
        self.data_path = fc.data_path
        self.array_index = fc.array_index
        self.keyframe_points = []

        for kp in fc.keyframe_points:
            self.keyframe_points.append(
                FakeKeyframePoint(kp)
            )


class FakeKeyframePoint:
    """用于分析的关键帧副本"""

    def __init__(self, kp):
        self.co = kp.co.copy()
        self.interpolation = kp.interpolation
        self.handle_left = kp.handle_left.copy()
        self.handle_right = kp.handle_right.copy()


@timeit
def copy_fcurves(fcurves):
    """深拷贝FCurve，仅用于分析，不影响原动画数据"""

    return [
        FakeFCurve(fc)
        for fc in fcurves
    ]


@timeit
def clean_fcurves(fcurves, threshold=0.03):
    for fcurve in fcurves:
        points = fcurve.keyframe_points

        if len(points) <= 2:
            return

        remove_points = []

        for i in range(1, len(points) - 1):
            prev = points[i - 1]
            curr = points[i]
            next = points[i + 1]

            # 根据前后关键帧线性插值计算当前帧理论值
            frame = curr.co.x
            t = (frame - prev.co.x) / (next.co.x - prev.co.x)

            expected = prev.co.y + (next.co.y - prev.co.y) * t

            # 实际值和线性预测值的误差
            error = abs(curr.co.y - expected)

            if error <= threshold:
                remove_points.append(curr)

        # 倒序删除，避免索引变化
        for kp in reversed(remove_points):
            points.remove(kp)
    return fcurves
