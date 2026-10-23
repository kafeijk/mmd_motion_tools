from ..operators.bone_operators import SimplifyBoneOperator, FlipPoseOperator, SelectLinkedBoneOperator
from ..operators.camera_motion_fixer_operators import FindCameraSegmentOperator, CameraMotionFixerOperator
from ..operators.clipping_detection_operators import ClippingDetectionOperator, ClearClippingOperator, \
    ShowClippingTypeOperator, get_detection_progress
from ..operators.motion_fixer_operators import MotionFixerOperator, SyncRangeOperator, RemoveOuterKeyframesOperator, \
    RemoveInnerKeyframesOperator, DeselectKeyframesOperator
from ..operators.utility_operators import ImportVmdPhysicsOperator, RemovePhysicalBoneAnimationOperator, \
    MutePhysicalBoneAnimationOperator, UnmutePhysicalBoneAnimationOperator, \
    FindRepeatedMotionOperator, ClearRepeatedOperator, SyncKeyframesOperator, FindRepeatedMotionStrongOperator, \
    GetSelectedBoneNameOperator
from ..properties.clipping_detection_properties import get_frame_hits_map
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

        if is_mmd_tools_enabled():
            row = col.row(align=True)
            split = row.split(factor=0.5, align=True)

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

        row = col.row(align=True)
        row.operator(ImportVmdPhysicsOperator.bl_idname, text=ImportVmdPhysicsOperator.bl_label, icon='ADD')
        row.operator(RemovePhysicalBoneAnimationOperator.bl_idname,
                     text=RemovePhysicalBoneAnimationOperator.bl_label, icon='REMOVE')

        row = col.row(align=True)
        row.operator(MutePhysicalBoneAnimationOperator.bl_idname,
                     text=MutePhysicalBoneAnimationOperator.bl_label, icon='HIDE_ON')
        row.operator(UnmutePhysicalBoneAnimationOperator.bl_idname,
                     text=UnmutePhysicalBoneAnimationOperator.bl_label, icon='HIDE_OFF')


def get_detection_range_groups(props):
    """
    按「手部 / 小臂 / 大臂」把检测范围分组

    items 怎么排，面板上的按钮就怎么排。默认勾选哪些（default）只决定按钮亮不亮，
    """
    range_props = props.bl_rna.properties["detection_range"]

    groups = {}
    for item in range_props.enum_items:
        identifier = item.identifier
        if "-" not in identifier:
            continue

        # 标识符形如 HAND-TORSO；显示名形如「手部-躯干」
        part, target = identifier.split("-", 1)
        name_parts = item.name.split("-", 1)
        group_label = name_parts[0] if len(name_parts) == 2 else part
        target_label = name_parts[1] if len(name_parts) == 2 else target
        if target_label == "自定义拓展":
            target_label = "自定义"

        groups.setdefault(group_label, []).append((identifier, target_label))

    return list(groups.items())


def draw_detection_range_grid(layout, props):
    """
    按属性面板的分栏方式画检测范围：左列右对齐部位名，右列放两个按钮

    Blender 的 use_property_split 内部用的是 factor=0.4 的 split，
    这里用同样的 0.4，并让左侧 label 右对齐，手部/小臂/大臂 的文本
    就能和上面的「时间范围」「代理精度」等属性标签竖向对齐。
    """
    # 按钮自己就是方块文字，不需要 use_property_split 的 40% 标签栏，
    # 否则 prop_enum 会在我们手工分好的 0.4 右侧再切一刀，把按钮压窄一半
    grid_col = layout.column(align=True)
    grid_col.use_property_split = False

    for group_label, items in get_detection_range_groups(props):
        for start in range(0, len(items), 2):
            row = grid_col.row(align=True)
            split = row.split(factor=0.4, align=True)

            # 方法名 40%：与属性标签同一条分栏线，右对齐
            label_row = split.row()
            label_row.alignment = 'RIGHT'
            label_row.label(text=f"检测项-{group_label}" if start == 0 else "")

            # 右侧 60%：两个按钮直接相邻排列，使用 Blender 默认的小间距。
            # 只有 1 个按钮时，才按 50% 分格，把右半格留空，避免它撑满整行。
            value_row = split.row(align=True)
            pair = items[start:start + 2]

            if len(pair) == 2:
                for identifier, target_label in pair:
                    value_row.prop_enum(props, "detection_range", identifier, text=target_label)
            else:
                single_split = value_row.split(factor=0.5, align=True)

                left_cell = single_split.row(align=True)
                identifier, target_label = pair[0]
                left_cell.prop_enum(props, "detection_range", identifier, text=target_label)

                # 占住右半格，单个按钮只占左半边
                right_cell = single_split.row(align=True)
                right_cell.label(text="")


class ClippingDetection_PT_Panel(bpy.types.Panel):
    bl_label = "穿模检测"
    bl_idname = "MOTIONTOOLS_PT_detect_clipping"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_parent_id = "MOTIONTOOLS_PT_motion_fixer"
    bl_order = 0

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

        # 「检测预设」和「代理精度」并排一行：左边标签按 40% 分栏、右对齐，
        # 和「自定义拓展」「代理精度」等其它参数的标签落在同一条线上；
        preset_row = col.row(align=True)
        preset_row.use_property_split = False
        preset_split = preset_row.split(factor=0.4, align=True)

        preset_label = preset_split.row()
        preset_label.alignment = 'RIGHT'
        preset_label.label(text="检测预设")

        preset_value = preset_split.row(align=True)
        preset_value.prop(props, "detection_preset", text="")
        preset_value.prop(props, "proxy_accuracy", text="")
        col.prop(props, "use_hand_sphere")

        range_toggle_row = col.row(align=True)
        range_toggle_row.prop(props, "show_detection_range")
        if props.show_detection_range:
            # 自定义拓展和检测项放在一个 box 里，背景色与其它参数区分开
            box = col.box()
            box.use_property_split = True
            box.use_property_decorate = False

            custom_row = box.row(align=True)
            custom_row.prop(props, "custom_bones_str")
            custom_row.operator(GetSelectedBoneNameOperator.bl_idname, text="", icon="MOUSE_LMB")
            custom_row.operator(SelectLinkedBoneOperator.bl_idname, text="", icon="LINKED")

            draw_detection_range_grid(box, props)
        col.prop(props, "remove_tmp")
        row = col.row(align=True)
        row.operator(ClippingDetectionOperator.bl_idname, text=ClippingDetectionOperator.bl_label)
        if len(props.clippings) != 0:
            row.operator(ClearClippingOperator.bl_idname, text=ClearClippingOperator.bl_label)

        progress = get_detection_progress()
        if progress["active"]:
            if hasattr(col, "progress"):
                col.progress(factor=progress["factor"], type='BAR', text=progress["text"])
            else:
                col.label(text=progress["text"])

        if len(props.clippings) != 0:
            # 结果显示样式
            mode_row = col.row(align=True)
            mode_row.use_property_split = False
            mode_row.prop(props, "result_list_mode", expand=True)

            # 原生列表的控件没注册上时（比如热重载后类表没刷新）自动退回分页窗口，
            # 免得 template_list 静默失败、整个结果框凭空消失
            if props.result_list_mode == 'PAGED' or not can_draw_native_list(props):
                # 专注模式：每页 5 项（一项 = 列表里的一条结果，对应一段连续穿模帧）。
                # 页按 clippings 顺序固定分组，一项在页里的行号始终不变。
                # 当前帧一旦不属于本页任何一项，就整页换到它所在的那一页：刚离开本页最后一项、
                # 还停在它后面的空档里也算，不用等它走进下一页的第一项；连续播放就是一页页往后翻
                list_box = col.box()
                list_box.use_property_split = False
                rows_col = list_box.column(align=True)
                for item in get_clipping_items(props, scene.frame_current):
                    draw_clipping_row(rows_col, props, item, scene.frame_current)
            else:
                rows = min(len(props.clippings), 5)
                col.template_list("MMD_UL_ClippingRange", "", props, "clippings", props, "clipping_index", rows=rows)


class FindRepeatedMotion_PT_Panel(bpy.types.Panel):
    bl_label = "查找重复动作"
    bl_idname = "MOTIONTOOLS_PT_find_repeated_motion"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_parent_id = "MOTIONTOOLS_PT_motion_fixer"
    bl_order = 1

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
        # 有结果时两个查找按钮先禁用（免得白跑一遍），同时把「清除」露出来；
        # 没结果时「清除」不占位置，面板保持干净
        find_row = row.row(align=True)
        find_row.enabled = len(props.repeated) == 0
        find_row.operator(FindRepeatedMotionOperator.bl_idname, text=FindRepeatedMotionOperator.bl_label)
        find_row.operator(FindRepeatedMotionStrongOperator.bl_idname, text=FindRepeatedMotionStrongOperator.bl_label)
        if len(props.repeated) != 0:
            row.operator(ClearRepeatedOperator.bl_idname, text=ClearRepeatedOperator.bl_label)


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

        if not is_mmd_tools_enabled():
            return

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


# 部件对 -> 图标
# 「手」用 VIEW_PAN，「小臂」用 MESH_CAPSULE
# 顺序有意义：先匹配到的生效
CLIPPING_ICONS = (
    ("HAND", "VIEW_PAN"),
    ("FOREARM", "MESH_CAPSULE"),
    ("UPPER_ARM", "MESH_CAPSULE"),
)


def get_clipping_icons(types):
    """
    把 ["HAND-TORSO", "FOREARM-HEAD"] 这样的部件对列表按图标分组

    返回字典而不是集合，这样点按钮时能只显示与该图标对应的那部分类型
    """
    icons = {}
    for item_type in types:
        part = item_type.split("-")[0]
        for keyword, icon_name in CLIPPING_ICONS:
            if keyword in part:
                icons.setdefault(icon_name, []).append(item_type)
                break
    return icons


# 结果列表一次显示几项（和原来 template_list 的 rows=5 一致）
CLIPPING_LIST_ROWS = 5


def get_clipping_items(props, frame, rows=CLIPPING_LIST_ROWS):
    """
    结果列表中，这一刻要画的 5 项：按 clippings 的顺序每 5 项分一页，跟着当前帧所在的页走

    这里的「项」就是列表里的一条结果（clippings 里的一项，对应一段连续的穿模帧）。

    - 页是固定分组（第 0-4 项一页、第 5-9 项一页……），所以每一项在页里的行号是固定的：
      点「起止帧」跳到同一页里的另一项时，列表不会重排
    - 当前帧一旦不属于本页任何一项，就整页换成它所在的那一页：刚离开本页最后一项、
      还停在它后面的空档里也算翻页，不用等它走进下一页的第一项
    - 当前帧落在两项之间的空档里时，按它后面最近的那一项算它在哪一页
    - 最后一页不足 5 项时往回补几项，保证框里始终是 5 行

    返回 [要画的项, ...]。
    """
    items = props.clippings
    count = len(items)
    if not count:
        return []  # 还没有检测结果，什么都不画

    # 1. 先找出「当前帧属于哪一项」：start / end 把当前帧包住的那一项
    #    next(生成器, None) 是「取第一个满足条件的值，取不到就给 None」
    index = next(
        (index for index, item in enumerate(items) if item.start <= frame <= item.end),
        None,
    )

    # 2. 当前帧没落在任何一项里时，用「它后面最近的那一项」代表它：
    #    这样刚离开本页最后一项、还没走进下一项（中间的空档）时，也能马上算出该翻到哪一页；
    #    后面已经没有项（当前帧比所有项都晚）就退回最后一项
    if index is None:
        index = next(
            (index for index, item in enumerate(items) if item.start > frame),
            count - 1,
        )

    # 3. 把「第几项」换算成「第几页的第一项」：
    #    index // rows 是它落在第几页（0 起），再乘回 rows 就是这一页第一项的下标。
    #    例（rows=5）：index=7 → 7//5=1（第 1 页）→ start=5（从第 5 项开始画）
    #    最后一页不满 5 项时改用 count-rows 往回补，保证框里始终画满 5 行；
    #    count 比 rows 还少时 max(..., 0) 兜底成 0，也就是全部画出来
    start = min(index // rows * rows, max(count - rows, 0))

    # 4. 从这一页的第一项往后连续取 rows 项；
    #    末尾的 min(...) 是防止最后一页越界，多出来的那几项直接不取
    return [items[offset] for offset in range(start, min(start + rows, count))]


def draw_clipping_row(layout, props, item, frame):
    """画结果列表的一行：起止帧按钮 + 这一项里出现过的穿模类型图标"""
    row = layout.row(align=True)
    split = row.split(factor=0.8)

    subrow = split.row(align=True)
    subrow.operator("mmd_motion_tools.jump_frame", text=str(item.start)).frame = item.start
    subrow.operator("mmd_motion_tools.jump_frame", text=str(item.end)).frame = item.end

    # 当前帧落在这一段的区间内时，取逐帧结果
    frame_types = get_frame_hits_map(props).get(frame) if item.start <= frame <= item.end else None

    # 右侧：图标
    subrow = split.row(align=True)
    # 图标始终按整段的类型汇总显示（段里存的是逗号串，这里拆成列表再分组）
    icons = get_clipping_icons(split_names(item.types))
    # 当前帧命中的那部分，只用来决定哪个图标变成可点的按钮
    hit_icons = get_clipping_icons(frame_types) if frame_types else {}

    # 始终按指定顺序显示
    for icon_name in ("VIEW_PAN", "MESH_CAPSULE"):
        if icon_name not in icons:
            subrow.label(text="", icon="BLANK1")
        elif icon_name in hit_icons:
            # 当前帧命中：图标变成按钮，点开后显示这一帧具体的穿模位置
            op = subrow.operator(ShowClippingTypeOperator.bl_idname, text="", icon=icon_name)
            op.frame = frame
            op.types = ", ".join(hit_icons[icon_name])
        else:
            # 当前帧没命中这一项：只提示这段出现过这类穿模
            subrow.label(text="", icon=icon_name)


class MMD_UL_ClippingRange(bpy.types.UIList):
    """穿模检测结果列表（可滚动的那一版，和下面的分页窗口共用同一套行内容）"""

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        draw_clipping_row(layout, data, item, context.scene.frame_current)


def can_draw_native_list(props):
    """
    原生滚动列表能不能画：UIList 类型和 clipping_index 都注册上了才算

    template_list 在「列表类型没找到」或「active 属性不存在」时不会报错，
    只是往控制台打一行 warning，然后什么都不画——结果框会整个消失，
    所以画之前先自己检查一遍，缺东西就退回分页窗口
    """
    return (hasattr(bpy.types, "MMD_UL_ClippingRange")
            and "clipping_index" in props.bl_rna.properties)


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
