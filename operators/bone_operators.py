from ..utils import *


class SimplifyBoneOperator(bpy.types.Operator):
    bl_idname = "mmd_motion_tools.simplify_bone"
    bl_label = "精简骨骼"
    bl_description = "隐藏不参与K帧的骨骼"
    bl_options = {'REGISTER', 'UNDO'}

    display_type: bpy.props.EnumProperty(
        name="显示为",
        description="显示骨骼的样式",
        items=[
            ("OCTAHEDRAL", "八面锥", "八面锥"),
            ("STICK", "棍形", "棍形"),
        ],
        default="STICK",
    )

    bake_mode: bpy.props.EnumProperty(
        name="显示范围",
        description="参与K帧的骨骼",
        items=[
            ("ARM1", "手臂", "选择手臂骨骼（不含捩骨）"),
            ("ARM2", "手臂（含捩骨）", "选择手臂骨骼（包含捩骨）"),
            ("KEYFRAME", "K帧骨骼", "选择用于K帧的常用骨骼"),
            ("DEFAULT", "默认骨骼", "选择导入Blender后默认显示的骨骼"),
        ],
        default="ARM1",
    )

    def execute(self, context):
        armatures = self.check_props(context)
        if not armatures:
            return {'CANCELLED'}

        for armature in armatures:
            if ".dummy_armature" in armature.name:
                continue
            select_bake_bone(armature, self.bake_mode, self.display_type)
        return {'FINISHED'}

    def check_props(self, context):
        if not is_mmd_tools_enabled():
            self.report({'ERROR'}, "未启用mmd_tools插件！")
            return None

        obj = context.active_object

        if not obj:
            self.report({'ERROR'}, "请选择模型!")
            return None

        root = find_ancestor(obj)
        armatures = find_children(root, "ARMATURE")
        if not armatures:
            self.report({'ERROR'}, "未找到模型骨架")
            return None

        return armatures


def select_bake_bone(armature, mode, display_type):
    """选择用于烘焙动作的骨骼"""
    bone_modes = {
        "ARM1": PMX_BAKE_BONES_ARM1,
        "ARM2": PMX_BAKE_BONES_ARM2,
        "KEYFRAME": PMX_BAKE_BONES,
    }

    if mode in bone_modes:
        bake_bones = set(bone_modes[mode])

        for pb in armature.pose.bones:
            is_bake_bone = pb.mmd_bone.name_j in bake_bones
            # 保留预设骨骼，隐藏其他骨骼
            set_bone_hide(pb, not is_bake_bone)
            armature.data.display_type = display_type
    else:
        for pb in armature.pose.bones:
            set_bone_hide(pb, pb.mmd_bone.is_tip)
            armature.data.display_type = display_type


class FlipPoseOperator(bpy.types.Operator):
    bl_idname = "mmd_motion_tools.flip_pose"
    bl_label = "翻转姿态"
    bl_description = "翻转姿态"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        armature = self.check_props(context)
        if not armature:
            return {'CANCELLED'}
        self.flip_bone(armature)
        return {'FINISHED'}

    def flip_bone(self, armature):
        # 未启用 MMD Tools 则当做普通模型
        if not is_mmd_tools_enabled():
            bpy.ops.pose.copy()
            bpy.ops.pose.paste(flipped=True)
            return

        root = find_pmx_root_with_child(armature)
        if root:
            bpy.ops.mmd_tools.flip_pose()
        else:
            bpy.ops.pose.copy()
            bpy.ops.pose.paste(flipped=True)

    def get_armature(self, context):
        obj = context.active_object

        if not obj or obj.type != 'ARMATURE':
            self.report({'ERROR'}, '请选择骨架对象!')
            return None

        if context.mode != 'POSE':
            self.report({'ERROR'}, '请进入姿态模式!')
            return None

        bones = get_selected_bones(obj)
        if not bones:
            self.report({'ERROR'}, '请选择骨骼!')
            return None

        return obj

    def check_props(self, context):
        armature = self.get_armature(context)
        if not armature:
            return None

        return armature
