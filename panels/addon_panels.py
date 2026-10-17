from ..operators.bone_operators import SimplifyBoneOperator, FlipPoseOperator
from ..operators.camera_motion_fixer_operators import FindCameraSegmentOperator, CameraMotionFixerOperator
from ..operators.clipping_detection_operators import ClippingDetectionOperator, ClearClippingOperator
from ..operators.motion_fixer_operators import MotionFixerOperator, SyncRangeOperator, RemoveOuterKeyframesOperator, \
    RemoveInnerKeyframesOperator, DeselectKeyframesOperator
from ..operators.utility_operators import ImportVmdPhysicsOperator, RemovePhysicalBoneAnimationOperator, \
    MutePhysicalBoneAnimationOperator, UnmutePhysicalBoneAnimationOperator, \
    FindRepeatedMotionOperator, ClearRepeatedOperator, SyncKeyframesOperator, FindRepeatedMotionStrongOperator, \
    GetSelectedBoneNameOperator
from ..utils import *


class ToolsPanel(bpy.types.Panel):
    bl_label = "通用工具"
    bl_idname = "MOTIONTOOLS_PT_tools"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'MMD Motion'
    bl_order = 0

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        col = layout.column()

        row = col.row(align=True)
        split = row.split(factor=0.5, align=True)
        try:
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
        except Exception:
            pass

        row = col.row(align=True)
        split = row.split(factor=0.5, align=True)
        left = split.row(align=True)
        left.operator(SimplifyBoneOperator.bl_idname, text=SimplifyBoneOperator.bl_label, icon="GROUP_BONE")
        right = split.row(align=True)
        right.operator(FlipPoseOperator.bl_idname, text=FlipPoseOperator.bl_label, icon="ARROW_LEFTRIGHT")

        try:
            row = col.row(align=True)
            row.operator("mmd_tools.import_vmd", text="动作导入", icon='ANIM')
            row.operator("mmd_tools.export_vmd", text="动作导出", icon='ANIM')
        except Exception:
            pass

        row = col.row(align=True)
        row.operator(ImportVmdPhysicsOperator.bl_idname, text=ImportVmdPhysicsOperator.bl_label, icon='ADD')
        row.operator(RemovePhysicalBoneAnimationOperator.bl_idname,
                     text=RemovePhysicalBoneAnimationOperator.bl_label, icon='REMOVE')

        row = col.row(align=True)
        row.operator(MutePhysicalBoneAnimationOperator.bl_idname,
                     text=MutePhysicalBoneAnimationOperator.bl_label, icon='HIDE_ON')
        row.operator(UnmutePhysicalBoneAnimationOperator.bl_idname,
                     text=UnmutePhysicalBoneAnimationOperator.bl_label, icon='HIDE_OFF')


class FindRepeatedMotion_PT_Panel(bpy.types.Panel):
    bl_label = "查找重复动作"
    bl_idname = "MOTIONTOOLS_PT_find_repeated_motion"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_parent_id = "MOTIONTOOLS_PT_motion_fixer"
    bl_order = 0

    def draw(self, context):
        scene = context.scene
        props = scene.mmd_motion_tools_find_repeated_motion

        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        col = layout.column()

        if len(props.repeated) != 0:
            rows = min(len(props.repeated), 5)
            col.template_list("MMD_UL_RepeatedRange", "", props, "repeated", props, "repeated_index", rows=rows)

        row = col.row(align=True)
        row.operator(FindRepeatedMotionOperator.bl_idname, text=FindRepeatedMotionOperator.bl_label)
        row.operator(FindRepeatedMotionStrongOperator.bl_idname, text=FindRepeatedMotionStrongOperator.bl_label)
        if len(props.repeated) != 0:
            row.enabled = False
        else:
            row.enabled = True
        row = col.row(align=True)
        row.operator(ClearRepeatedOperator.bl_idname, text=ClearRepeatedOperator.bl_label)

class ClippingDetection_PT_Panel(bpy.types.Panel):
    bl_label = "穿模检测"
    bl_idname = "MOTIONTOOLS_PT_detect_clipping"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_parent_id = "MOTIONTOOLS_PT_motion_fixer"
    bl_order = 1

    def draw(self, context):
        scene = context.scene
        props = scene.mmd_motion_tools_detect_clipping

        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        col = layout.column()

        sub = col.row(align=True)
        sub.scale_x = 0.8
        if not scene.use_preview_range:
            sub.prop(scene, "frame_start", text="时间范围")
            sub.prop(scene, "frame_end", text="")
        col.prop(props, "proxy_accuracy")

        if props.show_detection_range:
            custom_row = col.row(align=True)
            custom_row.prop(props, "custom_bones_str")
            custom_row.operator(GetSelectedBoneNameOperator.bl_idname, text="", icon="MOUSE_LMB")

            col.prop(props, "detection_range")

        col.prop(props, "show_detection_range")
        col.prop(props, "remove_tmp")
        col.operator(ClippingDetectionOperator.bl_idname, text=ClippingDetectionOperator.bl_label)

        if len(props.clippings) != 0:
            rows = min(len(props.clippings), 5)
            col.template_list("MMD_UL_ClippingRange", "", props, "clippings", props, "clipping_index", rows=rows)
            col.operator(ClearClippingOperator.bl_idname, text=ClearClippingOperator.bl_label)



class MotionFixer_PT_Panel(bpy.types.Panel):
    bl_label = "模型动作修复"
    bl_idname = "MOTIONTOOLS_PT_motion_fixer"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'MMD Motion'
    bl_order = 1

    def draw(self, context):
        scene = context.scene

        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        col = layout.column()

        row = col.row(align=True)
        row.operator(SyncRangeOperator.bl_idname, text=SyncRangeOperator.bl_label)
        row.operator(DeselectKeyframesOperator.bl_idname, text=DeselectKeyframesOperator.bl_label)

        row = col.row(align=True)
        row.operator(RemoveInnerKeyframesOperator.bl_idname, text=RemoveInnerKeyframesOperator.bl_label)
        row.operator(RemoveOuterKeyframesOperator.bl_idname, text=RemoveOuterKeyframesOperator.bl_label)

        col.operator(MotionFixerOperator.bl_idname, text=MotionFixerOperator.bl_label)


class CameraMotionFixer_PT_Panel(bpy.types.Panel):
    bl_label = "相机运动修复"
    bl_idname = "MOTIONTOOLS_PT_camera_motion_fixer"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'MMD Motion'
    bl_order = 2

    def draw(self, context):
        obj = context.active_object

        layout = self.layout
        layout.use_property_split = True

        try:
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

                col = layout.column()
                col.operator(FindCameraSegmentOperator.bl_idname, text=FindCameraSegmentOperator.bl_label)
                col.operator(CameraMotionFixerOperator.bl_idname, text=CameraMotionFixerOperator.bl_label)

            else:
                layout.operator("mmd_tools.convert_to_mmd_camera", text="Convert")
        except Exception as e:
            pass


class AboutPanel(bpy.types.Panel):
    bl_idname = "MOTIONTOOLS_PT_about"
    bl_label = "About"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'  # N面板
    bl_category = 'MMD Motion'  # 追加到其它面板或独自一个面板
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
                                  addon.bl_info['name'] == 'MMD Motion Tools'][0]))
        col.label(text='作者：KafeiMMD')


class MMD_UL_ClippingRange(bpy.types.UIList):
    """穿模检测结果列表"""

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        frame = context.scene.frame_current
        row = layout.row(align=True)
        split = row.split(factor=0.8, align=True)

        # 当前帧落在这一段的区间内时加个标记，方便对照视图
        active = item.start <= frame <= item.end

        subrow = split.row(align=True)
        subrow.operator("mmd_motion_tools.jump_frame", text=str(item.start)).frame = item.start
        subrow.operator("mmd_motion_tools.jump_frame", text=str(item.end)).frame = item.end

        icons = set()
        for item_type in item.types.split(","):
            part = item_type.strip().split("-")[0]
            if "HAND" in part:
                icons.add("VIEW_PAN")
            elif "FOREARM" in part:
                icons.add("MESH_CAPSULE")
            else:
                pass

        # 右侧：图标
        subrow = split.row(align=True)
        # 始终按指定顺序显示
        if "VIEW_PAN" in icons:
            subrow.label(text="", icon="VIEW_PAN")
        else:
            subrow.label(text="", icon="BLANK1")

        if "MESH_CAPSULE" in icons:
            subrow.label(text="", icon="MESH_CAPSULE")
        else:
            subrow.label(text="", icon="BLANK1")




class MMD_UL_RepeatedRange(bpy.types.UIList):

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        frame = context.scene.frame_current
        row = layout.row(align=True)

        row.operator("mmd_motion_tools.jump_frame", text=str(item.start)).frame = item.start
        row.operator("mmd_motion_tools.jump_frame", text=str(item.end)).frame = item.end

        button = row.row(align=True)
        button.ui_units_x = 1

        active = (item.start <= frame <= item.end or
                  item.repeat_start <= frame <= item.repeat_end)

        op = button.operator(SyncKeyframesOperator.bl_idname, text="➜" if active else "→")
        op.start = item.start
        op.end = item.end
        op.repeat_start = item.repeat_start
        op.repeat_end = item.repeat_end

        row.operator("mmd_motion_tools.jump_frame", text=str(item.repeat_start)).frame = item.repeat_start
        row.operator("mmd_motion_tools.jump_frame", text=str(item.repeat_end)).frame = item.repeat_end
