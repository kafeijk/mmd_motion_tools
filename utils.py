import functools
import importlib
import os
import re
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

CONVERT_NAME_TO_L_REGEXP = re.compile('^(.*)左(.*)$')
CONVERT_NAME_TO_R_REGEXP = re.compile('^(.*)右(.*)$')

# 姿态骨骼数据路径前缀
BONE_PATH_PREFIX = 'pose.bones["'

# 骨架对象上保存骨骼原始显示/隐藏状态的自定义属性名
ORIGINAL_BONE_HIDE_PROP = 'mmd_motion_tools_original_bone_hide'


def timeit(func):
    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        start = time.perf_counter()

        result = func(*args, **kwargs)

        end = time.perf_counter()

        print(f"{func.__name__} 执行耗时: {end - start:.6f} 秒")

        return result

    return wrapper


# -------------------------------------------------------------
# Action / F-Curve 版本兼容层
# -------------------------------------------------------------
# Blender 5.0 起 Action 改为 Slotted Action，原生移除了 Action.fcurves，
# F-Curve 分散存放在 action.layers[].strips[].channelbag(slot).fcurves 中。
# 本插件同时支持 3.2 ~ 5.x，因此所有涉及 Action.fcurves 的地方都改用
# get_action_fcurves / new_action_fcurve / remove_action_fcurves 三个入口。
#
# 版本判定说明：不能用 hasattr(bpy.types.Action, "fcurves") 探测——
# 在 5.x 上类级探测会返回 False，但 mmd_tools 的 compat 层会在实例上
# 注入 fcurves 属性，导致判定结果与实际可用性不一致。因此这里统一
# 以"实例 + try/except"为准，并优先走 channelbag 官方路径。

# Blender 5.0 引入 Slotted Action
SLOTTED_ACTION_VERSION = (5, 0, 0)


def _is_slotted_action(action):
    """判断 Action 是否为5.0+ 的 Slotted Action（以实例属性为准）"""
    return bpy.app.version >= SLOTTED_ACTION_VERSION


def iter_action_fcurves(action):
    """
    遍历 Action 的全部 F-Curve，跨版本通用。

    Blender 3.2 ~ 4.x：直接使用 action.fcurves。
    Blender 5.0+：遍历 layers -> strips -> slots -> channelbag，
                   按 (data_path, array_index) 去重后返回。
    """
    if action is None:
        return

    # 5.0+ 优先走官方 channelbag 路径
    if _is_slotted_action(action):
        found = False
        for layer in action.layers:
            for strip in layer.strips:
                # channelbag() 需要 ActionSlot 对象，不能用 handle（int）
                for slot in action.slots:
                    try:
                        channelbag = strip.channelbag(slot)
                    except (AttributeError, RuntimeError, TypeError):
                        continue

                    if channelbag is None:
                        continue

                    found = True
                    for fcurve in channelbag.fcurves:
                        yield fcurve
        if found:
            return

    # 3.x / 4.x 原生路径；5.x 上若 mmd_tools 注入过 fcurves 也能走这里
    try:
        legacy_fcurves = action.fcurves
    except AttributeError:
        return

    for fcurve in legacy_fcurves:
        yield fcurve


def get_action_fcurves(action):
    """
    返回 Action 的 F-Curve 列表，跨版本通用。

    action 可以是 Action 对象，也可以是带 animation_data 的对象
    （骨架、相机、Empty 等）；传入对象时自动取其 animation_data.action。
    无动画数据时返回空列表。

    按 (data_path, array_index) 去重，避免同一曲线被重复返回。
    """
    if action is None:
        return []

    # 传入的是对象而非 Action 时，取出其 Action
    if not isinstance(action, bpy.types.Action):
        animation_data = getattr(action, "animation_data", None)
        action = animation_data.action if animation_data else None

        if action is None:
            return []

    result = []
    seen = set()

    for fcurve in iter_action_fcurves(action):
        key = (fcurve.data_path, fcurve.array_index)

        if key in seen:
            continue

        seen.add(key)
        result.append(fcurve)

    return result


def new_action_fcurve(action, data_path, array_index=0):
    """
    在 Action 中新建 F-Curve，跨版本通用。成功返回 F-Curve，失败返回 None。

    Blender 3.x / 4.x：走 action.fcurves.new()。
    Blender 5.0+：Action 原生移除了 fcurves 集合，必须落到具体的
                   channelbag 上，通过 channelbag.fcurves.new() 新建。

    注意：5.x 上新创建的 Action 可能还没有 layer/strip/slot，
    此时本函数返回 None，由调用方决定是否需要先初始化 Action。
    """
    if action is None:
        return None

    try:
        return action.fcurves.new(data_path=data_path, index=array_index)
    except AttributeError:
        pass

    # 4.2 及更早没有 layers/slots
    if not hasattr(action, "layers"):
        return None

    for layer in action.layers:
        for strip in layer.strips:
            for slot in action.slots:
                try:
                    channelbag = strip.channelbag(slot)
                except (AttributeError, RuntimeError, TypeError):
                    continue

                if channelbag is None:
                    continue

                return channelbag.fcurves.new(data_path=data_path,
                                              index=array_index)

    return None


def remove_action_fcurves(action, fcurves):
    """
    从 Action 中删除指定的 F-Curve，跨版本通用。返回成功删除的数量。

    Blender 3.x / 4.x：走 action.fcurves.remove()。
    Blender 5.0+：F-Curve 归属于具体 channelbag，
                   需逐个定位其所属 channelbag 后再删除。
    """
    if action is None or not fcurves:
        return 0

    # 按键去重，避免同一条曲线被重复处理
    remaining = {}

    for fcurve in fcurves:
        remaining.setdefault((fcurve.data_path, fcurve.array_index), fcurve)

    if not remaining:
        return 0

    removed = 0

    # 3.x / 4.x 的原生路径
    try:
        legacy_fcurves = action.fcurves
    except AttributeError:
        legacy_fcurves = None

    if legacy_fcurves is not None:
        doomed = [
            c for c in legacy_fcurves
            if (c.data_path, c.array_index) in remaining
        ]

        for target in doomed:
            try:
                legacy_fcurves.remove(target)
                removed += 1
                remaining.pop((target.data_path, target.array_index), None)
            except (RuntimeError, ReferenceError, ValueError):
                continue

    # 5.0+ 原生路径：定位每条曲线所属的 channelbag 后删除。
    # 若 legacy 路径已全部处理完，remaining 为空，循环会自然跳过。
    # 4.2 及更早没有 layers/slots 属性，直接跳过。
    if remaining and hasattr(action, "layers"):
        for layer in action.layers:
            for strip in layer.strips:
                for slot in action.slots:
                    try:
                        channelbag = strip.channelbag(slot)
                    except (AttributeError, RuntimeError, TypeError):
                        continue

                    if channelbag is None:
                        continue

                    doomed = [
                        c for c in channelbag.fcurves
                        if (c.data_path, c.array_index) in remaining
                    ]

                    for target in doomed:
                        try:
                            channelbag.fcurves.remove(target)
                            removed += 1
                            remaining.pop(
                                (target.data_path, target.array_index), None)
                        except (RuntimeError, ReferenceError, ValueError):
                            continue

    return removed


# -------------------------------------------------------------
# mmd_tools 版本兼容层
# -------------------------------------------------------------
# mmd_tools 在不同 Blender 版本下的包名不同（与 is_mmd_tools_enabled 保持一致）：
#   Blender 3.x:  mmd_tools
#   Blender 4.2:  bl_ext.user_default.mmd_tools
#   Blender 4.3+: bl_ext.blender_org.mmd_tools
#
# 除了包名，VMD 导入在 3.6（源码版本 ~0.x/3.x）与 4.2+（4.5.14）之间还有以下实质差异：
#
# 1. VMDImporter 构造参数改名/新增
#    3.6  : use_NLA, convert_mmd_lamp
#    4.2+ : use_nla, convert_mmd_light, detect_camera_changes, detect_light_changes
#    ——两者共有的是 filepath / scale / bone_mapper / use_pose_mode / frame_margin，
#      兼容层只传这些共有参数，不写版本号分支。
#
# 2. __assignToArmature / __assign_action 的写入方式完全不同（最关键）
#    3.6  : action = bpy.data.actions.new(...)          ——总是新建 Action 并挂到骨架
#    4.2+ : action = __get_or_create_action(...)        ——骨架上已有 Action 时直接复用，
#           且 __assign_action 在 target.animation_data.action 存在时直接 return，
#           于是关键帧被写进“调用前就存在的那个 Action”。
#    ——用结构探测（有没有 _VMDImporter__get_or_create_action）区分，而不是硬编码版本号。
#
# 3. 帧号公式不同（属于各版本官方行为，这里刻意保留而不做跨版本归一化）
#    3.6  : frame = key.frame_number + frame_margin + 1
#    4.2+ : frame = key.frame_number + frame_start + frame_margin
#                   （frame_start = 当前帧，且 frame_margin 仅在当前帧为 0/1 时生效）
#    ——物理帧必须和同一台 Blender 上导入的动作帧对齐，所以沿用本机 mmd_tools 的算法。
#
# 4. 数据结构：core/vmd 中的骨骼动画在两版都是 BoneAnimation(defaultdict)、
#    IK 开关都是 PropertyAnimation(list)、容器都是 File.load(filepath=...)；
#    唯一改名的是 3.6 的 lampAnimation → 4.2+ 的 lightAnimation（物理导入不使用）。

MMD_TOOLS_PACKAGE_NAMES = (
    'mmd_tools',  # Blender 3.x 传统插件
    'bl_ext.blender_org.mmd_tools',  # Blender 4.3+ 扩展
    'bl_ext.user_default.mmd_tools',  # Blender 4.2 过渡期
)


class MmdToolsModules:
    """mmd_tools 中读取/转换 VMD 所需的模块集合，跨版本统一入口"""

    __slots__ = ('package_name', 'vmd', 'VMDImporter', 'makePmxBoneMap')

    def __init__(self, package_name, vmd_module, importer_class, bone_mapper):
        self.package_name = package_name
        self.vmd = vmd_module
        self.VMDImporter = importer_class
        self.makePmxBoneMap = bone_mapper

    @property
    def writes_existing_action(self):
        """
        True 表示 VMDImporter 会直接把关键帧写进骨架上已有的 Action（4.2+），
        False 表示总是新建一个临时 Action 再挂到骨架上（3.6）。

        用结构探测而不是版本号判断：只有在 4.2+ 的 importer 里才有 __get_or_create_action，而这个私有方法正是“复用已有 Action”的开关。
        """
        return hasattr(self.VMDImporter, '_VMDImporter__get_or_create_action')

    def new_importer(self, filepath, scale=0.08, frame_margin=0):
        """构造 VMDImporter（构造过程会一次性解析整个 vmd），失败时抛出原始异常"""
        return self.VMDImporter(
            filepath=filepath,
            scale=scale,
            bone_mapper=self.makePmxBoneMap,
            frame_margin=frame_margin,
        )

    def get_parsed_file(self, importer):
        """取出 importer 内部已经解析好的 vmd.File 实例"""
        file_obj = getattr(importer, '_VMDImporter__vmdFile', None)

        if isinstance(file_obj, self.vmd.File):
            return file_obj

        # 兜底：不同版本的私有变量名可能不同，直接在实例字典里找回 vmd.File
        for value in vars(importer).values():
            if isinstance(value, self.vmd.File):
                return value

        return None


def load_mmd_tools_modules():
    """
    按包名顺序尝试加载 mmd_tools 的 VMD 相关模块。

    返回 MmdToolsModules；mmd_tools 未安装/未启用或版本不匹配时返回 None。
    """
    for package_name in MMD_TOOLS_PACKAGE_NAMES:
        try:
            vmd_module = importlib.import_module(package_name + '.core.vmd')
            importer_module = importlib.import_module(package_name + '.core.vmd.importer')
            utils_module = importlib.import_module(package_name + '.utils')
        except ImportError:
            continue

        importer_class = getattr(importer_module, 'VMDImporter', None)
        bone_mapper = getattr(utils_module, 'makePmxBoneMap', None)

        if importer_class is None or bone_mapper is None:
            continue

        return MmdToolsModules(package_name, vmd_module, importer_class, bone_mapper)

    return None


def get_bl_jp_map(armature, bone_mapper):
    """
    返回 {blender 骨骼名: VMD/PMX 日文名}。

    直接复用 mmd_tools 的 makePmxBoneMap 得到 {日文名: pose_bone} 再反转，
    保证筛选 VMD 骨骼时的名称规则与 mmd_tools 自身导入时完全一致。
    """
    pmx_map = bone_mapper(armature) or {}

    result = {}
    for name_j, pose_bone in pmx_map.items():
        result.setdefault(pose_bone.name, name_j)

    return result


def get_action_bone_fcurves(action, bone_names):
    """返回 Action 中属于指定骨骼的 F-Curve 列表。bone_names 为可迭代对象。"""
    if action is None:
        return []

    bone_names = set(bone_names)
    result = []

    for fcurve in get_action_fcurves(action):
        data_path = fcurve.data_path

        if not data_path.startswith(BONE_PATH_PREFIX):
            continue

        if data_path.split('"', 2)[1] in bone_names:
            result.append(fcurve)

    return result


def remove_bone_fcurves(action, bone_names):
    """移除 Action 中指定骨骼的全部 F-Curve，返回成功删除的数量"""
    if action is None or not bone_names:
        return 0

    return remove_action_fcurves(action, get_action_bone_fcurves(action, bone_names))


def set_fcurve_group(action, fcurve, source_group):
    """把源 F-Curve 的分组信息同步到目标 F-Curve，失败静默（分组仅影响编辑器观感）"""
    group_name = getattr(source_group, 'name', None)

    if not group_name:
        return

    try:
        group = None
        for existing in action.groups:
            if existing.name == group_name:
                group = existing
                break

        if group is None:
            group = action.groups.new(group_name)

        fcurve.group = group
    except (AttributeError, RuntimeError, TypeError, ValueError):
        pass


def assign_action(obj, action):
    """
    把 Action 挂到对象上，跨版本通用。

    Blender 5.0 起 Action 是 Slotted Action，动画数据必须绑定到具体的 action_slot，
    否则即使 Action 里已经有 F-Curve，骨骼也读不到任何动画。
    """
    animation_data = obj.animation_data
    if animation_data is None:
        animation_data = obj.animation_data_create()

    animation_data.action = action

    if action is None or not hasattr(animation_data, 'action_slot'):
        return action

    try:
        # action_slot 从 Blender 4.4 起存在，已有 slot 时不覆盖用户的选择
        if animation_data.action_slot is None and hasattr(action, 'slots'):
            for slot in action.slots:
                animation_data.action_slot = slot
                break
    except (RuntimeError, TypeError, ValueError):
        pass

    return action


def ensure_action(obj, action_name=None):
    """确保对象持有 Action，返回该 Action（没有时新建并挂载）"""
    animation_data = obj.animation_data
    if animation_data is None:
        animation_data = obj.animation_data_create()

    action = animation_data.action
    if action is None:
        action = bpy.data.actions.new(name=action_name or obj.name)
        assign_action(obj, action)

    return action


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
    """显示物体"""
    set_visibility(obj, (False, False, False, False))


def hide_object(obj):
    """隐藏物体"""
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


def set_keyframe_selected(kp, selected):
    """设置关键帧的选中状态，三个标记同时设置才生效"""
    kp.select_control_point = selected
    kp.select_left_handle = selected
    kp.select_right_handle = selected


# foreach_get / foreach_set 在个别版本上不支持布尔、浮点数组属性，
# 这些异常都表示"批量读写不可用"，统一退回逐帧读写。
_FOREACH_ERRORS = (AttributeError, TypeError, ValueError, RuntimeError, NotImplementedError)


def get_fcurve_frames(fcurve):
    """
    读取一条 F-Curve 上所有关键帧的帧号（float），一次 RNA 调用批量取回。
    不支持批量读取时退回逐帧读取。
    """
    keyframe_points = fcurve.keyframe_points
    count = len(keyframe_points)

    if count == 0:
        return []

    try:
        # co 为 (frame, value) 对，展开成一维数组后取偶数位即帧号
        co = [0.0] * (count * 2)
        keyframe_points.foreach_get('co', co)
        return co[0::2]
    except _FOREACH_ERRORS:
        return [kp.co[0] for kp in keyframe_points]


def set_fcurve_keyframes_selected(fcurve, selected):
    """
    批量设置一条 F-Curve 上所有关键帧的选中状态（True 选中 / False 取消选中）。
    返回设置后处于选中状态的关键帧数量。

    优先用 foreach_set 整条曲线一次写入（每通道 1 次 RNA 调用），
    而不是每个关键帧 3 次；不支持批量写入时退回逐帧写入。
    """
    keyframe_points = fcurve.keyframe_points
    count = len(keyframe_points)

    if count == 0:
        return 0

    values = [bool(selected)] * count

    try:
        keyframe_points.foreach_set('select_control_point', values)
        keyframe_points.foreach_set('select_left_handle', values)
        keyframe_points.foreach_set('select_right_handle', values)
    except _FOREACH_ERRORS:
        for kp in keyframe_points:
            set_keyframe_selected(kp, selected)

    return count if selected else 0


def get_fcurve_selection_flags(fcurve):
    """
    批量读取一条 F-Curve 上所有关键帧的选中状态，返回 bool 列表。
    不支持批量读取时返回 None，由调用方退回逐帧读取。
    """
    keyframe_points = fcurve.keyframe_points
    count = len(keyframe_points)

    if count == 0:
        return []

    try:
        control_points = [False] * count
        left_handles = [False] * count
        right_handles = [False] * count

        keyframe_points.foreach_get('select_control_point', control_points)
        keyframe_points.foreach_get('select_left_handle', left_handles)
        keyframe_points.foreach_get('select_right_handle', right_handles)
    except _FOREACH_ERRORS:
        return None

    return [
        bool(cp or lh or rh)
        for cp, lh, rh in zip(control_points, left_handles, right_handles)
    ]


def select_fcurve_keyframes_in_range(fcurve, frame_start, frame_end):
    """
    选中 [frame_start, frame_end] 区间内的关键帧，区间外的一律取消选中。
    返回被选中的关键帧数量。
    """
    keyframe_points = fcurve.keyframe_points
    count = len(keyframe_points)

    if count == 0:
        return 0

    frames = get_fcurve_frames(fcurve)
    values = [frame_start <= frame <= frame_end for frame in frames]

    try:
        keyframe_points.foreach_set('select_control_point', values)
        keyframe_points.foreach_set('select_left_handle', values)
        keyframe_points.foreach_set('select_right_handle', values)
    except _FOREACH_ERRORS:
        for kp, selected in zip(keyframe_points, values):
            set_keyframe_selected(kp, selected)

    return sum(values)


def is_keyframe_selected(kp):
    """判断关键帧是否处于选中状态，三个标记中任一为真即视为选中"""
    return bool(kp.select_control_point or kp.select_left_handle or kp.select_right_handle)


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


def get_bone_hide(bone):
    """读取骨骼隐藏状态，兼容 Blender 5.0"""
    if bpy.app.version >= (5, 0, 0):
        return bool(bone.hide)
    else:
        return bool(bone.bone.hide)


def save_original_bone_hide(armature):
    """
    在骨架对象上记录所有骨骼原始的显示/隐藏状态。

    只在首次调用（属性不存在或为空）时写入，已有值时不再覆盖，
    """
    if armature is None:
        return

    # 已有记录则跳过，避免把精简后的状态误当成原始状态
    if armature.get(ORIGINAL_BONE_HIDE_PROP):
        return

    states = {}
    for pb in armature.pose.bones:
        # 骨骼名为空或超长时无法作为自定义属性键
        if not pb.name:
            continue
        states[pb.name] = get_bone_hide(pb)

    if not states:
        return

    try:
        armature[ORIGINAL_BONE_HIDE_PROP] = states
    except (AttributeError, RuntimeError, TypeError, ValueError):
        pass


def get_original_bone_hide(armature):
    """读取骨架上记录的骨骼原始显示/隐藏状态，返回 {骨骼名: 是否隐藏}，无记录时返回空字典"""
    if armature is None:
        return {}

    states = armature.get(ORIGINAL_BONE_HIDE_PROP)
    if not states:
        return {}

    # IDPropertyGroup 转为普通字典，便于按骨骼名取值
    to_dict = getattr(states, "to_dict", None)
    if callable(to_dict):
        states = to_dict()
    else:
        states = {name: states[name] for name in states.keys()}

    return {name: bool(hidden) for name, hidden in states.items()}


def restore_original_bone_hide(armature):
    """
    按骨架上记录的原始状态恢复骨骼显示/隐藏。
    成功恢复返回 True；没有记录（属性被删除等）时返回 False，由调用方兜底。
    """
    states = get_original_bone_hide(armature)
    if not states:
        return False

    for pb in armature.pose.bones:
        if pb.name not in states:
            continue
        set_bone_hide(pb, states[pb.name])

    return True


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

    for fcurve in get_action_fcurves(animation_data.action):
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

    for fcurve in get_action_fcurves(action):
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
    animation_data = armature.animation_data
    if not animation_data or not animation_data.action:
        return None
    action = animation_data.action

    # 当前旋转值
    pb = armature.pose.bones.get(bone_name)
    values = list(pb.rotation_quaternion)

    has_keyframe = False
    data_path = f'pose.bones["{bone_name}"].rotation_quaternion'

    for fcurve in get_action_fcurves(action):
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

    for fc in get_action_fcurves(animation_data.action):
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


def get_selected_frames(action, path, array_index=None, fcurves=None):
    """
    获取指定骨骼选中的关键帧范围。

      None  —— 该 path 下没有任何关键帧
      []    —— 有关键帧但选中不连续
      list  —— 连续选中的帧号列表
    """

    selected_frames = set()
    all_frames = set()

    if fcurves is None:
        fcurves = get_action_fcurves(action)

    for fc in fcurves:
        if not fc.data_path.startswith(path):
            continue
        if array_index is not None and fc.array_index != array_index:
            continue

        # 批量读取帧号与选中标记，每条曲线各 4 次 RNA 调用，
        # 而不是每个关键帧 4 次
        frames = get_fcurve_frames(fc)
        flags = get_fcurve_selection_flags(fc)

        if flags is None:
            flags = [is_keyframe_selected(kp) for kp in fc.keyframe_points]

        for frame, selected in zip(frames, flags):
            frame = int(frame)
            all_frames.add(frame)

            if selected:
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
            return []

    return sorted(selected_frames)


def apply_transform(obj, data_path, offset, frame_start, frame_end, current_value, array_index=None, margin=0):
    """应用动画变换修改"""
    animation_data = obj.animation_data
    if not animation_data or not animation_data.action:
        return

    action = animation_data.action

    fcurve_map = {}

    for fc in get_action_fcurves(action):
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

    for (data_path, index), fc in fcurve_map.items():
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
        remove_margin_keys(fcurve_map.values(), frame_start, frame_end, margin, update=False)


def set_keyframe_value(kp, value):
    """修改关键帧值并同步移动贝塞尔控制杆"""
    offset = value - kp.co[1]

    kp.co[1] = value
    kp.handle_left.y += offset
    kp.handle_right.y += offset


def remove_margin_keys(fcurves, frame_start, frame_end, margin, update=True):
    """删除范围边缘的关键帧"""
    for fc in fcurves:
        fc.update()

    # 当前曲线最小关键帧
    min_frame = min(int(kp.co[0]) for fc in fcurves for kp in fc.keyframe_points)

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
    if update:
        bpy.context.scene.frame_set(bpy.context.scene.frame_current)


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


def get_physical_bone(root):
    """获取受物理影响的骨骼"""
    rigidbody_parent = find_rigid_body_parent(root)
    if rigidbody_parent is None:
        return []
    rigid_bodies = rigidbody_parent.children
    # 受刚体物理影响的骨骼名称列表（blender名称）
    bl_names = []
    for rigidbody in rigid_bodies:
        # 存在有刚体但没有关联骨骼的情况
        if rigidbody.mmd_rigid.bone == '':
            continue
        # 0:骨骼 1:物理 2:物理+骨骼
        if rigidbody.mmd_rigid.type not in ('1', '2'):
            continue
        bl_names.append(rigidbody.mmd_rigid.bone)
    return bl_names


## 日本語で左右を命名されている名前をblender方式のL(R)に変更する
def convertNameToLR(name, use_underscore=False):
    m = CONVERT_NAME_TO_L_REGEXP.match(name)
    delimiter = '_' if use_underscore else '.'
    if m:
        name = m.group(1) + m.group(2) + delimiter + 'L'
    m = CONVERT_NAME_TO_R_REGEXP.match(name)
    if m:
        name = m.group(1) + m.group(2) + delimiter + 'R'
    return name


def copy_obj(obj, col=None):
    new_mesh = obj.data.copy()
    new_obj = obj.copy()
    new_obj.data = new_mesh
    if not col:
        col = obj.users_collection[0]
    col.objects.link(new_obj)
    return new_obj


def import_vmd(filepath, scale=0.08, margin=0):
    filepath = bpy.path.abspath(filepath)

    kwargs = {
        "filepath": filepath,
        "directory": os.path.dirname(filepath),
        "files": [{"name": os.path.basename(filepath)}],
        "scale": scale,
        "margin": margin,
    }

    if bpy.app.version >= (4, 2, 0):
        kwargs["create_new_action"] = True

    return bpy.ops.mmd_tools.import_vmd('EXEC_DEFAULT', **kwargs)
