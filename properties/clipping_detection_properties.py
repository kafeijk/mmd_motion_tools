from bpy.app.handlers import persistent

from ..utils import *


@persistent
def reset_clipping_index(scene, depsgraph):
    props = scene.mmd_motion_tools_detect_clipping

    if props.clipping_index != -1:
        props.clipping_index = -1


def sync_custom_detection_range(props):
    """「自定义拓展」从空变为有值时，自动勾上检测范围里所有含「自定义拓展」的项"""
    if not props.custom_bones_str.strip():
        return

    enum_items = props.bl_rna.properties["detection_range"].enum_items
    custom_keys = {
        item.identifier
        for item in enum_items
        if item.identifier.endswith("-CUSTOM")
    }
    if not custom_keys:
        return

    current = set(props.detection_range)
    # 判断 custom_keys 是否是 current 的子集
    if custom_keys <= current:
        return

    props.detection_range = current | custom_keys


def keep_detection_range(props):
    """
    检测范围至少保留一项：把最后一项也取消时，回滚到上一次的选择

    上一次的选择记在 detection_range_backup 里（RNA 属性，能跟着 .blend 一起保存），
    否则重开文件后模块级缓存就没了，只有一项时取消会误跳到默认值。
    """
    current = set(props.detection_range)

    if current:
        backup = ",".join(sorted(current))
        if props.detection_range_backup != backup:
            props.detection_range_backup = backup
        return

    # 优先回滚到上一次的选择；没有历史（例如旧文件里存的就是空值）时恢复成属性定义里的 default
    fallback = set(split_names(props.detection_range_backup))
    if not fallback:
        fallback = set(props.bl_rna.properties["detection_range"].default_flag)
    if not fallback:
        return

    props.detection_range = fallback


class ClippingRangeItem(bpy.types.PropertyGroup):
    """一段连续的穿模区间"""
    start: bpy.props.IntProperty(name="起始帧")
    end: bpy.props.IntProperty(name="结束帧")
    # 这段里出现过的部件对名称，多个用 ", " 分隔
    # （PropertyGroup 存不了列表，所以拼成字符串）
    types: bpy.props.StringProperty(name="穿模部件对")


class ClippingFrameHit(bpy.types.PropertyGroup):
    """逐帧结果里的一条命中记录：一帧命中几个部件对就有几条"""
    frame: bpy.props.IntProperty(name="帧")
    # 单个部件对名称，如 "HAND-TORSO"
    # 一项一条而不是拼成字符串，读的时候直接按帧 append 成列表，不需要再拆逗号
    name: bpy.props.StringProperty(name="穿模部件对")


class ClippingDetectionProperty(bpy.types.PropertyGroup):
    proxy_accuracy: bpy.props.EnumProperty(
        name="代理精度",
        description="代理网格的精度比例",
        items=[
            ("MINIMAL", "极简", "极简"),
            ("BALANCED", "均衡", "均衡"),
            ("FULL", "完整", "完整"),
        ],
        default="BALANCED",
    )

    # 检测范围：多选
    detection_range: bpy.props.EnumProperty(
        name="检测范围",
        description="选择参与穿模检测的部件对，可多选",
        items=[
            ("HAND-TORSO", "手部-躯干", "手部-躯干"),
            ("HAND-LOWER_LIMBS", "手部-下肢", "手部-下肢"),
            ("HAND-HEAD", "手部-头部", "手部-头部"),
            ("HAND-UPPER_ARM", "手部-大臂", "手部-大臂"),
            ("HAND-BREAST", "手部-胸部", "手部-胸部"),
            # 同一部件的左右互检：左手 vs 右手
            ("HAND-HAND", "手部-手部间", "左手与右手之间"),
            ("HAND-CUSTOM", "手部-自定义拓展", "手部-自定义拓展"),

            ("FOREARM-TORSO", "小臂-躯干", "小臂-躯干"),
            ("FOREARM-LOWER_LIMBS", "小臂-下肢", "小臂-下肢"),
            ("FOREARM-HEAD", "小臂-头部", "小臂-头部"),
            ("FOREARM-BREAST", "小臂-胸部", "小臂-胸部"),
            # 同一部件的左右互检：左小臂 vs 右小臂
            ("FOREARM-FOREARM", "小臂-小臂间", "左小臂与右小臂之间"),
            ("FOREARM-CUSTOM", "小臂-自定义拓展", "小臂-自定义拓展"),

            ("UPPER_ARM-TORSO", "大臂-躯干", "大臂-躯干"),
            ("UPPER_ARM-LOWER_LIMBS", "大臂-下肢", "大臂-下肢"),
            ("UPPER_ARM-HEAD", "大臂-头部", "大臂-头部"),
            ("UPPER_ARM-BREAST", "大臂-胸部", "大臂-胸部"),
            ("UPPER_ARM-CUSTOM", "大臂-自定义拓展", "大臂-自定义拓展"),
        ],
        options={'ENUM_FLAG'},
        default={
            "HAND-TORSO",
            "HAND-LOWER_LIMBS",
            "HAND-HEAD",
            "HAND-UPPER_ARM",
            "HAND-BREAST",
            "FOREARM-TORSO",
            "FOREARM-LOWER_LIMBS",
            "FOREARM-HEAD",
            "FOREARM-BREAST",
            "UPPER_ARM-LOWER_LIMBS",
            "UPPER_ARM-HEAD",
            "UPPER_ARM-BREAST"
        },
        update=lambda self, context: keep_detection_range(self),
    )

    # 上一次非空的检测范围（内部使用）：逗号分隔的标识符
    detection_range_backup: bpy.props.StringProperty(
        name="上次检测范围",
        description="内部使用：记录上一次非空的检测范围，用于阻止把检测范围取消到空",
        default="",
    )

    # 自定义拓展：把额外的骨骼（顶点组）当作独立部件 CUSTOM 参与检测，
    custom_bones_str: bpy.props.StringProperty(
        name="自定义拓展",
        description="通过额外的骨骼名称，扩展检测范围中的「自定义拓展」部件\n"
                    "在此处输入骨骼名称，并以中文逗号或英文逗号作为分隔",
        default="",
        update=lambda self, context: sync_custom_detection_range(self),
    )

    # 是否在面板上显示「检测范围」
    show_detection_range: bpy.props.BoolProperty(
        name="显示检测范围",
        description="显示检测范围选项",
        default=False,
    )

    # 检测结束后是否删掉临时生成的部件代理网格
    remove_tmp: bpy.props.BoolProperty(
        name="删除临时对象",
        description="删除临时对象",
        default=True
    )

    # 是否生成手部罩体，并把它和手部一起当作「真正的手」参与检测
    use_hand_sphere: bpy.props.BoolProperty(
        name="手部罩体",
        description="在左右手各生成一个罩体包裹手部，罩体代替手部参与穿模检测",
        default=False,
    )

    # 穿模检测结果
    clippings: bpy.props.CollectionProperty(
        type=ClippingRangeItem
    )

    # 逐帧穿模结果：{帧: 穿模类型} 的可持久化版本
    # （集合本身没法当字典查，UIList 里用 get_frame_hits_map 转成字典）
    frame_hits: bpy.props.CollectionProperty(
        type=ClippingFrameHit
    )

    # 逐帧结果的版本号：每次检测后自增，用来让 get_frame_hits_map 的缓存失效
    frame_hits_version: bpy.props.IntProperty(default=0)

    # 列表里当前选中项的索引
    clipping_index: bpy.props.IntProperty()

    @staticmethod
    def register():
        bpy.types.Scene.mmd_motion_tools_detect_clipping = bpy.props.PointerProperty(type=ClippingDetectionProperty)
        bpy.app.handlers.depsgraph_update_post.append(reset_clipping_index)

    @staticmethod
    def unregister():
        del bpy.types.Scene.mmd_motion_tools_detect_clipping
        bpy.app.handlers.depsgraph_update_post.remove(reset_clipping_index)


def get_ordered_detection_range(props):
    """
    把 detection_range 按「items 里定义时的顺序」排好再返回

    RNA 里的 enum_items 保留了 items 的原始定义顺序，用它当排序键即可。
    这样以后在 items 里调整顺序 / 增删条目，这里不需要跟着改。
    """
    enum_items = props.bl_rna.properties["detection_range"].enum_items
    order = {item.identifier: index for index, item in enumerate(enum_items)}
    # 兜底：老工程里可能残留已被删除的标识符，统一排到最后
    unknown = len(order)
    return sorted(props.detection_range, key=lambda identifier: order.get(identifier, unknown))


# frame_hits 集合 -> 字典的缓存
# 键是 (属性组地址, 版本号)，每次检测后版本号自增，旧缓存自然失效
_frame_hits_cache = {}


def set_frame_hits(props, frame_hits):
    """把 {帧: [穿模类型, ...]} 写进 props.frame_hits，并让之前的字典缓存失效"""
    props.frame_hits.clear()
    for frame in sorted(frame_hits):
        # 一项一条：同一帧命中多个部件对就是多条记录，不拼字符串
        for name in frame_hits[frame]:
            hit = props.frame_hits.add()
            hit.frame = frame
            # 没传部件对名称时这里是 None，存成空串更安全
            hit.name = name or ""

    props.frame_hits_version += 1
    _frame_hits_cache.clear()


def clear_clipping_results(props):
    """清除检测结果：区间列表和逐帧结果一起清"""
    props.clippings.clear()
    props.frame_hits.clear()
    props.frame_hits_version += 1
    _frame_hits_cache.clear()


def get_frame_hits_map(props):
    """把 frame_hits 集合转成 {帧: [穿模类型, ...]} 的字典"""
    key = (props.as_pointer(), props.frame_hits_version)
    cached = _frame_hits_cache.get(key)
    if cached is not None:
        return cached

    frame_hits = {}
    for hit in props.frame_hits:
        frame_hits.setdefault(hit.frame, []).append(hit.name)

    # 多场景时会有多个键，留几个够用，超出就整体清掉（旧版本的键反正也不会再被查到）
    if len(_frame_hits_cache) > 8:
        _frame_hits_cache.clear()
    _frame_hits_cache[key] = frame_hits
    return frame_hits


def get_clipping_type_labels(props, types):
    """
    把 "HAND-TORSO" 这类部件对标识符换成界面上的名称（如「手部-躯干」）

    名称直接取 detection_range 里定义的第二项，以后改文案不用同步维护
    """
    enum_items = props.bl_rna.properties["detection_range"].enum_items
    labels = {item.identifier: item.name for item in enum_items}
    return [labels.get(name, name) for name in split_names(types)]
