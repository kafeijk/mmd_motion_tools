from ..utils import *


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
