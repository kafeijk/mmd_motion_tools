from bpy.app.handlers import persistent

from ..utils import *


@persistent
def reset_clipping_index(scene, depsgraph):
    props = scene.mmd_motion_tools_detect_clipping

    if props.clipping_index != -1:
        props.clipping_index = -1


class ClippingRangeItem(bpy.types.PropertyGroup):
    """一段连续的穿模区间"""
    start: bpy.props.IntProperty(name="起始帧")
    end: bpy.props.IntProperty(name="结束帧")
    # 这段里出现过的部件对名称，多个用 ", " 分隔
    # （PropertyGroup 存不了列表，所以拼成字符串）
    types: bpy.props.StringProperty(name="穿模部件对")


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
            ("HAND-CUSTOM", "手部-自定义拓展", "手部-自定义拓展"),

            ("FOREARM-TORSO", "小臂-躯干", "小臂-躯干"),
            ("FOREARM-LOWER_LIMBS", "小臂-下肢", "小臂-下肢"),
            ("FOREARM-HEAD", "小臂-头部", "小臂-头部"),
            ("FOREARM-BREAST", "小臂-胸部", "小臂-胸部"),
            ("FOREARM-CUSTOM", "小臂-自定义拓展", "小臂-自定义拓展"),

            ("UPPER_ARM-TORSO", "大臂-躯干", "大臂-躯干"),
            ("UPPER_ARM-LOWER_LIMBS", "大臂-下肢", "大臂-下肢"),
            ("UPPER_ARM-HEAD", "大臂-头部", "大臂-头部"),
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
        },
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

    # 穿模检测结果
    clippings: bpy.props.CollectionProperty(
        type=ClippingRangeItem
    )

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
