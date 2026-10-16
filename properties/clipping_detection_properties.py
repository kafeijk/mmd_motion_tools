from ..utils import *


class ClippingDetectionProperty(bpy.types.PropertyGroup):
    mode: bpy.props.EnumProperty(
        name="检测模式",
        description="检测模式",
        items=[
            ("DEFAULT", "默认", "适用于骨架驱动网格的情形"),
            ("OTHER", "其它", "适用于缓存等其它方式驱动网格的情形"),
        ],
        default="DEFAULT",
    )
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
    @staticmethod
    def register():
        bpy.types.Scene.mmd_motion_tools_detect_clipping = bpy.props.PointerProperty(type=ClippingDetectionProperty)

    @staticmethod
    def unregister():
        del bpy.types.Scene.mmd_motion_tools_detect_clipping
