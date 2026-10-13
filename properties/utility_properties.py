import bpy
from bpy.app.handlers import persistent


@persistent
def reset_repeated_index(scene, depsgraph):
    props = scene.mmd_motion_tools_find_repeated_motion

    if props.repeated_index != -1:
        props.repeated_index = -1


class RepeatMotionItem(bpy.types.PropertyGroup):
    start: bpy.props.IntProperty()
    end: bpy.props.IntProperty()
    repeat_start: bpy.props.IntProperty()
    repeat_end: bpy.props.IntProperty()


class FindRepeatedMotionProperties(bpy.types.PropertyGroup):
    repeated: bpy.props.CollectionProperty(
        type=RepeatMotionItem
    )

    # 存储活动项索引
    repeated_index: bpy.props.IntProperty()

    @staticmethod
    def register():
        bpy.types.Scene.mmd_motion_tools_find_repeated_motion = bpy.props.PointerProperty(
            type=FindRepeatedMotionProperties)
        bpy.app.handlers.depsgraph_update_post.append(reset_repeated_index)

    @staticmethod
    def unregister():
        del bpy.types.Scene.mmd_motion_tools_find_repeated_motion
        bpy.app.handlers.depsgraph_update_post.remove(reset_repeated_index)
