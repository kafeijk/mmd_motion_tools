from ..utils import *


class FixMotionProperty(bpy.types.PropertyGroup):
    margin: bpy.props.IntProperty(
        name="边距",
        description="边缘关键帧范围",
        default=3,
        min=0,
    )

    @staticmethod
    def register():
        bpy.types.Scene.mmd_motion_tools_fix_motion = bpy.props.PointerProperty(type=FixMotionProperty)

    @staticmethod
    def unregister():
        del bpy.types.Scene.mmd_motion_tools_fix_motion
