import functools
import time

import addon_utils
import bpy
from mathutils import Quaternion, Vector, Euler

# -------------------------------------------------------------
# 特定用途骨骼
# -------------------------------------------------------------
# 用于烘焙VMD动作的MMD骨骼
# 名称取自MMR preset.json文件（上半身3并非MMD标准骨或次标准骨，但在预设文件中存在）
PMX_BAKE_BONES = ['全ての親', 'センター',
                  '左足ＩＫ', '左つま先ＩＫ', '右足ＩＫ', '右つま先ＩＫ',
                  '上半身', '上半身3', '上半身2', '首', '頭', '左目', '右目',
                  '左肩', '左腕', '左腕捩', '左ひじ', '左手捩', '左手首',
                  '右肩', '右腕', '右腕捩', '右ひじ', '右手捩', '右手首',
                  '左親指０', '左親指１', '左親指２', '左人指１', '左人指２', '左人指３', '左中指１', '左中指２', '左中指３',
                  '左薬指１', '左薬指２', '左薬指３', '左小指１', '左小指２', '左小指３',
                  '右親指０', '右親指１', '右親指２', '右人指１', '右人指２', '右人指３', '右中指１', '右中指２', '右中指３',
                  '右薬指１', '右薬指２', '右薬指３', '右小指１', '右小指２', '右小指３',
                  '下半身',
                  '左足', '左ひざ', '左足首', '左足先EX', '右足', '右ひざ', '右足首', '右足先EX']

PMX_BAKE_BONES_ARM1 = [
    '左肩', '左腕', '左ひじ', '左手首',
    '右肩', '右腕', '右ひじ', '右手首',
]

PMX_BAKE_BONES_ARM2 = [
    '左肩', '左腕', '左腕捩', '左ひじ', '左手捩', '左手首',
    '右肩', '右腕', '右腕捩', '右ひじ', '右手捩', '右手首',
]


def find_pmx_root():
    """寻找pmx对应空物体"""
    return next((obj for obj in bpy.context.scene.objects if obj.mmd_type == 'ROOT'), None)


def find_pmx_root_with_child(child):
    """根据child寻找pmx对应空物体"""
    if not child:
        return None

    parent = child
    while parent:
        if parent.mmd_type == 'ROOT':
            return parent
        parent = parent.parent

    return None


def find_pmx_armature(pmx_root):
    return next((child for child in pmx_root.children if child.type == 'ARMATURE'), None)


def find_pmx_objects(pmx_armature):
    return list((child for child in pmx_armature.children if child.type == 'MESH' and child.mmd_type == 'NONE'))


def find_rigid_body_parent(root):
    """寻找刚体对象"""
    return next(filter(lambda o: o.type == 'EMPTY' and o.mmd_type == 'RIGID_GRP_OBJ', root.children), None)


def find_joint_parent(root):
    return next(filter(lambda o: o.type == 'EMPTY' and o.mmd_type == 'JOINT_GRP_OBJ', root.children), None)


def is_mmd_camera_root(obj):
    return obj is not None and obj.type == "EMPTY" and obj.mmd_type == "CAMERA"


def find_mmd_camera_root(obj):
    if is_mmd_camera_root(obj):
        return obj

    if obj and is_mmd_camera_root(obj.parent):
        return obj.parent

    return None


def is_mmd_camera(obj):
    return obj is not None and obj.type == "CAMERA" and find_mmd_camera_root(obj.parent) is not None


def select_and_activate(obj):
    """选中并激活物体"""
    if bpy.context.active_object and bpy.context.active_object.mode != "OBJECT":
        bpy.ops.object.mode_set(mode='OBJECT')
    try:
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
    except RuntimeError:  # RuntimeError: 错误: 物体 'xxx' 不在视图层 'ViewLayer'中, 所以无法选中!
        pass


def deselect_all_objects():
    """对场景中的选中对象和活动对象取消选择"""
    if bpy.context.active_object is None:
        return
    bpy.ops.object.mode_set(mode='OBJECT')
    bpy.ops.object.select_all(action='DESELECT')
    bpy.context.view_layer.objects.active = None


def show_object(obj):
    """显示物体。在视图取消禁用选择，在视图中取消隐藏，在视图中取消禁用，在渲染中取消禁用"""
    set_visibility(obj, (False, False, False, False))


def hide_object(obj):
    """显示物体。在视图取消禁用选择，在视图中取消隐藏，在视图中取消禁用，在渲染中取消禁用"""
    set_visibility(obj, (True, True, True, True))


def set_visibility(obj, visibility):
    """设置Blender物体的可见性相关属性"""
    # 如果不在当前视图层，则跳过，如"在视图层中排除该集合"的情况下
    view_layer = bpy.context.view_layer
    # 成员资格测试（Python 会调用对象的 __eq__ 方法。）
    if obj.name not in view_layer.objects:
        return
    # 是否可选
    obj.hide_select = visibility[0]
    # 是否在视图中隐藏
    obj.hide_set(visibility[1])
    # 是否在视图中禁用
    obj.hide_viewport = visibility[2]
    # 是否在渲染中禁用
    obj.hide_render = visibility[3]


def get_selected_bones(armature):
    return [pb for pb in armature.pose.bones if is_pose_bone_selected(pb)]


def select_pose_bone(pb, status):
    """
    选中/取消选中姿态模式下骨骼

    Pose bones now have a select property that stores their selection state.
    (bpy.data.objects["Armature"].pose.bones[0].select)
    Selection is synced with edit bones when going in and out of Edit Mode.
    https://developer.blender.org/docs/release_notes/5.0/python_api/#animation-rigging

    """
    blender_version = bpy.app.version

    if blender_version < (5, 0, 0):
        pb.bone.select = status
    else:
        pb.select = status


def is_pose_bone_selected(pb):
    """
    获取姿态模式下骨骼的选中状态

    Pose bones now have a select property that stores their selection state.
    (bpy.data.objects["Armature"].pose.bones[0].select)
    Selection is synced with edit bones when going in and out of Edit Mode.
    https://developer.blender.org/docs/release_notes/5.0/python_api/#animation-rigging

    """
    blender_version = bpy.app.version
    if blender_version < (5, 0, 0):
        return pb.bone.select
    else:
        return pb.select


def set_bone_hide(bone, hide):
    """设置骨骼隐藏状态，兼容 Blender 5.0"""
    if bpy.app.version >= (5, 0, 0):
        bone.hide = hide
    else:
        bone.bone.hide = hide


def find_ancestor(obj):
    ancestor = obj
    while ancestor.parent is not None:
        ancestor = ancestor.parent
    return ancestor


def find_children(obj, obj_type=None):
    children = []
    if not obj_type:
        children.append(obj)
    else:
        if obj.type in obj_type:
            children.append(obj)

    for child in obj.children:
        children.extend(find_children(child, obj_type))
    return children


def is_mmd_tools_enabled():
    """
    校验mmd_tools是否开启，addon.module分别为：
    3.x版本 为 mmd_tools
    4.2版本 临时为 bl_ext.user_default.mmd_tools
    4.3版本及以后 bl_ext.blender_org.mmd_tools
    """
    for mod in addon_utils.modules():
        if mod.__name__.split(".")[-1] == "mmd_tools":
            return addon_utils.check(mod.__name__)[0]

    return False


def get_camera_value(obj, data_path, frame, array_index=None):
    """
    获取指定对象在指定帧的值。
    若存在对应动画曲线，则使用 FCurve 计算值。
    若未检测到对应关键帧，则返回 None。
    """
    animation_data = obj.animation_data
    if not animation_data or not animation_data.action:
        return None

    for fcurve in animation_data.action.fcurves:
        if fcurve.data_path != data_path:
            continue

        if not fcurve.keyframe_points:
            return None

        if array_index is not None and fcurve.array_index != array_index:
            continue

        return fcurve.evaluate(frame)

    return None


def get_camera_euler(obj, data_path, frame):
    """
    获取指定对象在指定帧的欧拉旋转。
    若存在对应动画曲线，则使用 FCurve 计算值。
    若未检测到对应旋转关键帧，则返回 None。
    """
    animation_data = obj.animation_data
    if not animation_data or not animation_data.action:
        return None

    action = animation_data.action

    values = [0.0, 0.0, 0.0]
    has_keyframe = False

    for fcurve in action.fcurves:
        if fcurve.data_path != data_path:
            continue

        if fcurve.keyframe_points:
            has_keyframe = True

        if fcurve.array_index < 3:
            values[fcurve.array_index] = fcurve.evaluate(frame)

    if not has_keyframe:
        return None

    return Euler(values, obj.rotation_mode)


def get_quaternion(armature, bone_name, frame):
    """
    获取指定骨骼在指定帧的四元数旋转。
    若存在对应关键帧，则使用关键帧值；否则使用当前变换中的旋转值。
    若未检测到该骨骼的旋转关键帧，则返回 None。
    """
    action = armature.animation_data.action
    if not action:
        return None

    # 当前旋转值
    pb = armature.pose.bones.get(bone_name)
    values = list(pb.rotation_quaternion)

    has_keyframe = False
    data_path = f'pose.bones["{bone_name}"].rotation_quaternion'

    for fcurve in action.fcurves:
        if fcurve.data_path == data_path:
            if fcurve.keyframe_points:
                has_keyframe = True

            index = fcurve.array_index
            values[index] = fcurve.evaluate(frame)

    if not has_keyframe:
        return None

    return Quaternion(values)


def get_location(obj, data_path, frame, default=None):
    """
    获取指定对象在指定帧的位置。
    若存在对应动画曲线，则使用 FCurve 计算值。
    若未检测到对应的位置关键帧，则返回 None。
    """
    animation_data = obj.animation_data
    if not animation_data or not animation_data.action:
        return None

    if default is None:
        default = Vector((0, 0, 0))

    values = [default[0], default[1], default[2]]

    found = False

    for fc in animation_data.action.fcurves:
        if fc.data_path != data_path:
            continue
        if fc.array_index >= 3:
            continue

        values[fc.array_index] = fc.evaluate(frame)
        found = True

    if not found:
        return None

    return Vector(values)


def calculate_euler_offset(e_start, e_target):
    return Euler(
        (
            e_target.x - e_start.x,
            e_target.y - e_start.y,
            e_target.z - e_start.z
        ),
        e_target.order
    )


def calculate_quaternion_offset(q_start, q_target):
    return q_target @ q_start.inverted()


def get_selected_frames(action, path, array_index=None):
    """获取指定骨骼选中的关键帧范围"""

    selected_frames = set()
    all_frames = set()

    for fc in action.fcurves:
        if not fc.data_path.startswith(path):
            continue
        if array_index is not None and fc.array_index != array_index:
            continue

        for kp in fc.keyframe_points:
            frame = int(kp.co[0])
            all_frames.add(frame)

            if kp.select_control_point or kp.select_left_handle or kp.select_right_handle:
                selected_frames.add(frame)

    # 无选中关键帧
    if not selected_frames:
        return None

    start = min(selected_frames)
    end = max(selected_frames)

    # 检查范围内是否存在未选中的关键帧
    unselected = all_frames - selected_frames
    for frame in unselected:
        if start < frame < end:
            return set()

    return sorted(selected_frames)


def timeit(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start = time.perf_counter()

        result = func(*args, **kwargs)

        end = time.perf_counter()

        print(f"{func.__name__} 执行耗时: {end - start:.6f} 秒")

        return result

    return wrapper


def apply_transform(obj, data_path, offset, frame_start, frame_end, current_value, array_index=None, margin=0):
    """应用动画变换修改"""
    animation_data = obj.animation_data
    if not animation_data or not animation_data.action:
        return

    action = animation_data.action

    fcurve_map = {}

    for fc in action.fcurves:
        if fc.data_path == data_path:
            # 对应单值，无论array_index传入何值，fcurve_map的array_index默认为0，避免处理逻辑不一致
            if array_index is not None and fc.array_index == array_index:
                fcurve_map[(fc.data_path, 0)] = fc
                continue
            fcurve_map[(fc.data_path, fc.array_index)] = fc
    if not fcurve_map:
        return

    # 单值转换为列表，避免处理逻辑不一致
    if isinstance(current_value, (int, float)):
        current_value = [current_value]
        # 值存储在对应索引位
        if array_index is not None:
            current_value = [None] * array_index + current_value

    # 获取通道数（维度）
    channels = len(current_value)
    frame_keys = {}

    for (data_path,index), fc in fcurve_map.items():
        for kp in fc.keyframe_points:
            frame = int(kp.co[0])
            if frame_start <= frame <= frame_end:
                if frame not in frame_keys:
                    frame_keys[frame] = [None] * channels
                frame_keys[frame][index] = kp

    for kps in frame_keys.values():
        # 若缺失通道，则使用当前对象值
        values = list(current_value)
        for index, kp in enumerate(kps):
            if kp:
                values[index] = kp.co[1]

        if isinstance(offset, Quaternion):
            value = offset @ Quaternion(values)
            value.normalize()
        elif isinstance(offset, Vector):
            value = Vector(values) + offset
        elif isinstance(offset, Euler):
            value = Euler(values, offset.order)
            value.x += offset.x
            value.y += offset.y
            value.z += offset.z
        elif isinstance(offset, (int, float)):
            value = list(values)
            for index, kp in enumerate(kps):
                if kp is not None:
                    value[index] += offset
        else:
            continue

        # 只修改含关键帧的通道
        for index, kp in enumerate(kps):
            if kp:
                set_keyframe_value(kp, value[index])

    for fc in fcurve_map.values():
        fc.update()

    if margin > 0:
        remove_margin_keys(fcurve_map.values(), frame_start, frame_end, margin)


def set_keyframe_value(kp, value):
    """修改关键帧值并同步移动贝塞尔控制杆"""
    offset = value - kp.co[1]

    kp.co[1] = value
    kp.handle_left.y += offset
    kp.handle_right.y += offset


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


def smooth_key_transition(fc, frame_pairs):
    """设置指定关键帧之间为线性过渡"""
    for left_frame, right_frame in frame_pairs:
        left_key, _ = find_adjacent_keys(fc, left_frame, right_frame)
        if left_key:
            left_key.interpolation = 'LINEAR'

    fc.update()


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
