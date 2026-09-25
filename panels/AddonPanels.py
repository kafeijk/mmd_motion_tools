from ..operators.bone_operators import SimplifyBoneOperator, FlipPoseOperator
from ..operators.motion_fixer_operators import MotionFixerOperator, CopyRangeOperator, RemoveMarginKeyFrameOperator
from ..utils import *


class ToolsPanel(bpy.types.Panel):
    bl_label = "通用工具"
    bl_idname = "MOTIONTOOLS_PT_tools"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'MotionTools'
    bl_order = 0

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        col = layout.column()

        row = col.row(align=True)
        split = row.split(factor=0.5, align=True)
        if is_mmd_tools_enabled():
            left = split.row(align=True)
            left.operator("mmd_tools.import_model", text="模型导入", icon="OUTLINER_OB_ARMATURE")

            right_setup = split.row(align=True)
            right_setup.operator("mmd_tools.morph_slider_setup", text="装配变形", icon="SHAPEKEY_DATA").type = "BIND"
            right_setup.operator("mmd_tools.morph_slider_setup", text="", icon="TRASH").type = "UNBIND"

            root = find_pmx_root_with_child(context.active_object)
            if root:
                right_setup.enabled = True
            else:
                right_setup.enabled = False

        row = col.row(align=True)
        split = row.split(factor=0.5, align=True)
        left = split.row(align=True)
        left.operator(SimplifyBoneOperator.bl_idname, text=SimplifyBoneOperator.bl_label, icon="GROUP_BONE")
        right = split.row(align=True)
        right.operator(FlipPoseOperator.bl_idname, text=FlipPoseOperator.bl_label, icon="ARROW_LEFTRIGHT")

        if is_mmd_tools_enabled():
            row = col.row(align=True)
            row.operator("mmd_tools.import_vmd", text="动作导入", icon='ANIM')
            row.operator("mmd_tools.export_vmd", text="动作导出", icon='ANIM')


class MotionFixer_PT_Panel(bpy.types.Panel):
    bl_label = "模型动作修复"
    bl_idname = "MOTIONTOOLS_PT_motion_fixer"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'MotionTools'
    bl_order = 1

    def draw(self, context):
        scene = context.scene

        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        col = layout.column()

        col.operator(CopyRangeOperator.bl_idname, text=CopyRangeOperator.bl_label)
        row = col.row(align=True)
        row.operator(MotionFixerOperator.bl_idname, text=MotionFixerOperator.bl_label)
        row.operator(RemoveMarginKeyFrameOperator.bl_idname, text=RemoveMarginKeyFrameOperator.bl_label)


class CameraMotionFixer_PT_Panel(bpy.types.Panel):
    bl_label = "相机运动修复"
    bl_idname = "MOTIONTOOLS_PT_camera_motion_fixer"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'MotionTools'
    bl_order = 2

    def draw(self, context):
        obj = context.active_object

        layout = self.layout
        layout.use_property_split = True

        if is_mmd_tools_enabled():
            mmd_camera_root = find_mmd_camera_root(obj)
            if mmd_camera_root:
                try:
                    from mmd_tools.core.camera import MMDCamera
                except ImportError:
                    try:
                        from bl_ext.blender_org.mmd_tools.core.camera import MMDCamera
                    except ImportError:
                        pass

                mmd_cam = MMDCamera(obj)
                empty = mmd_cam.object()
                camera = mmd_cam.camera()

                layout.prop(empty, "location")
                layout.prop(camera, "location", index=1, text="Distance")

                layout.prop(empty, "rotation_euler")

                layout.prop(empty.mmd_camera, "angle")
                layout.prop(empty.mmd_camera, "is_perspective")
            else:
                layout.operator("mmd_tools.convert_to_mmd_camera", text="Convert")


class AboutPanel(bpy.types.Panel):
    bl_idname = "MOTIONTOOLS_PT_about"
    bl_label = "About"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'  # N面板
    bl_category = 'MotionTools'  # 追加到其它面板或独自一个面板
    bl_order = 3
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        col = layout.column(align=True)

        # 版本号
        col.label(
            text='版本号：' + str([addon.bl_info.get('version', (-1, -1, -1)) for addon in addon_utils.modules() if
                                  addon.bl_info['name'] == 'mmd_motion_tools'][0]))
        col.label(text='作者：KafeiMMD')
