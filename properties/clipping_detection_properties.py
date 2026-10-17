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
            ("AUTO", "自动", "自动"),
            ("PER10", "10%", "代理精度为10%"),
            ("PER20", "20%", "代理精度为20%"),
            ("PER30", "30%", "代理精度为30%"),
            ("PER40", "40%", "代理精度为40%"),
            ("PER50", "50%", "代理精度为50%"),
            ("PER60", "60%", "代理精度为60%"),
            ("PER70", "70%", "代理精度为70%"),
            ("PER80", "80%", "代理精度为80%"),
            ("PER90", "90%", "代理精度为90%"),
            ("PER100", "100%", "代理精度为100%"),
        ],
        default="AUTO",
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
