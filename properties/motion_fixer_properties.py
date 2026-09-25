from ..utils import *


class FixMotionProperty(bpy.types.PropertyGroup):

    @staticmethod
    def register():
        bpy.types.Scene.mmd_motion_tools_fix_motion = bpy.props.PointerProperty(type=FixMotionProperty)

    @staticmethod
    def unregister():
        del bpy.types.Scene.mmd_motion_tools_fix_motion
