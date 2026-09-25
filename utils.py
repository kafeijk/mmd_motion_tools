import addon_utils
import bpy
from mathutils import Quaternion, Vector

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


def get_location(armature, bone_name, frame, default=None):
    """
    获取指定骨骼在指定帧的位置。
    若存在对应关键帧，则使用关键帧值；否则使用当前变换中的位置值。
    若未检测到该骨骼的位置关键帧，则返回 None。
    """

    action = armature.animation_data.action
    if not action:
        return None

    data_path = f'pose.bones["{bone_name}"].location'

    if default is None:
        default = Vector((0, 0, 0))

    values = [
        default[0],
        default[1],
        default[2]
    ]

    found = False

    for fc in action.fcurves:
        if fc.data_path == data_path:
            for kp in fc.keyframe_points:
                if int(kp.co[0]) == frame:
                    values[fc.array_index] = kp.co[1]
                    found = True
                    break

    if not found:
        return None

    return Vector(values)


def calculate_quaternion_offset(q_start, q_target):
    return q_target @ q_start.inverted()
