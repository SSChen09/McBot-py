"""物品组件（Data Components，1.20.5+/766+）跳过解析。

按 MCC StructuredComponentsHandler 原理移植，仅实现 skip（不消费语义、不回写）：
- 协议版本 → 注册表路由：766 → Registry1206、767 → Registry121、769+ → Registry1212。
- 组件无长度/ID 前缀包裹，靠注册表逐个从流中按字节格式跳过，错一字节即失步。
- NBT 跳过通过 skip_nbt 回调注入（复用 client._read_nbt，避免循环导入）。
- 组件数据只用基础原语：VarInt/Bool/Int/Long/Float/Double/String/UUID/NBT/Slot。
"""

from __future__ import annotations

from typing import Callable, Dict, Optional

from .datatypes import PacketBuffer, PacketReadError

# skip_nbt 回调类型：从流中跳过一个网络 NBT
SkipNbt = Callable[[PacketBuffer], None]

# 组件跳过函数签名：(buf, protocol, skip_nbt)
SkipFn = Callable[[PacketBuffer, int, Optional[SkipNbt]], None]


# ---------------------------------------------------------------------------
# 基础原语辅助
# ---------------------------------------------------------------------------


def _count(buf: PacketBuffer) -> int:
    """列表数量：负数按 0 处理（防御异常数据）。"""
    n = buf.read_varint()
    return n if n > 0 else 0


def _holder_set(buf: PacketBuffer) -> None:
    """HolderSet：VarInt t；t==0 → String 标签名；t>1 → (t-1) 个 VarInt id（t==1 为空集）。"""
    t = buf.read_varint()
    if t == 0:
        buf.read_string()
    elif t > 1:
        for _ in range(t - 1):
            buf.read_varint()


def _sound_event(buf: PacketBuffer) -> None:
    """SoundEvent：VarInt t；t!=0 注册表引用结束；t==0 → String + Bool(+Float)。"""
    if buf.read_varint() == 0:
        buf.read_string()
        if buf.read_bool():
            buf.read_float()


def _skip_slot(buf: PacketBuffer, protocol: int, skip_nbt: Optional[SkipNbt]) -> None:
    """内嵌 ItemStack（bundle/charged_projectiles/container/use_remainder 用），递归组件。"""
    from .datatypes import read_item_stack  # 延迟导入避免循环

    read_item_stack(buf, protocol, skip_nbt)


# ---------------------------------------------------------------------------
# 子组件（无自描述前缀，由父组件上下文决定格式）
# ---------------------------------------------------------------------------


def _sub_block_set(buf: PacketBuffer) -> None:
    """BlockSet：与 HolderSet 同构（t==0 → String tag；t>1 → (t-1)×VarInt）。"""
    _holder_set(buf)


def _sub_property(buf: PacketBuffer) -> None:
    """Property：String 名; Bool 精确匹配(+String) 否则 Bool min(+String)+Bool max(+String)。"""
    buf.read_string()
    if buf.read_bool():
        buf.read_string()
    else:
        if buf.read_bool():
            buf.read_string()
        if buf.read_bool():
            buf.read_string()


def _sub_block_predicate(buf: PacketBuffer, skip_nbt: Optional[SkipNbt] = None) -> None:
    """BlockPredicate：Bool(+BlockSet); Bool(+List<Property>); Bool(+NBT)。"""
    if buf.read_bool():
        _sub_block_set(buf)
    if buf.read_bool():
        for _ in range(_count(buf)):
            _sub_property(buf)
    if buf.read_bool():
        _read_nbt(buf, skip_nbt)


def _sub_attribute_legacy(buf: PacketBuffer) -> None:
    """Attribute（1.20.6 版）：VarInt 属性 id; UUID; String; Double; VarInt; VarInt。"""
    buf.read_varint()
    buf.read_bytes(16)
    buf.read_string()
    buf.read_double()
    buf.read_varint()
    buf.read_varint()


def _sub_attribute_121(buf: PacketBuffer) -> None:
    """Attribute（1.21+ 版）：VarInt; String; Double; VarInt; VarInt（无 UUID）。"""
    buf.read_varint()
    buf.read_string()
    buf.read_double()
    buf.read_varint()
    buf.read_varint()


def _sub_details(buf: PacketBuffer) -> None:
    """MobEffectDetails：VarInt 放大器; VarInt 时长; Bool×3; Bool 隐藏效果(+递归)。"""
    buf.read_varint()
    buf.read_varint()
    buf.read_bool()
    buf.read_bool()
    buf.read_bool()
    if buf.read_bool():
        _sub_details(buf)


def _sub_potion_effect(buf: PacketBuffer) -> None:
    """PotionEffect：VarInt 效果类型 id + Details。"""
    buf.read_varint()
    _sub_details(buf)


def _sub_effect(buf: PacketBuffer) -> None:
    """Effect（1.20.6 Food 用）：PotionEffect + Float 概率。"""
    _sub_potion_effect(buf)
    buf.read_float()


def _sub_rule(buf: PacketBuffer) -> None:
    """Tool Rule：BlockSet; Bool(+Float 速度); Bool(+Bool 掉落)。"""
    _sub_block_set(buf)
    if buf.read_bool():
        buf.read_float()
    if buf.read_bool():
        buf.read_bool()


def _sub_firework_explosion(buf: PacketBuffer) -> None:
    """FireworkExplosion：VarInt 形状; List<Int> 颜色; List<Int> 渐变; Bool 尾迹; Bool 闪烁。"""
    buf.read_varint()
    for _ in range(_count(buf)):
        buf.read_int()
    for _ in range(_count(buf)):
        buf.read_int()
    buf.read_bool()
    buf.read_bool()


def _sub_sound_event(buf: PacketBuffer) -> None:
    """SoundEvent 子组件（1.21+）：与 _sound_event 同构。"""
    _sound_event(buf)


# ---------------------------------------------------------------------------
# 组件 skip 实现
# ---------------------------------------------------------------------------


def _read_nbt(buf: PacketBuffer, skip_nbt: Optional[SkipNbt]) -> None:
    if skip_nbt is None:
        raise PacketReadError("组件含 NBT 但未注入 skip_nbt 回调")
    skip_nbt(buf)


def _s_empty(buf: PacketBuffer, protocol: int, skip_nbt: Optional[SkipNbt]) -> None:
    pass


def _s_nbt(buf: PacketBuffer, protocol: int, skip_nbt: Optional[SkipNbt]) -> None:
    _read_nbt(buf, skip_nbt)


def _s_varint(buf: PacketBuffer, protocol: int, skip_nbt: Optional[SkipNbt]) -> None:
    buf.read_varint()


def _s_bool(buf: PacketBuffer, protocol: int, skip_nbt: Optional[SkipNbt]) -> None:
    buf.read_bool()


def _s_int(buf: PacketBuffer, protocol: int, skip_nbt: Optional[SkipNbt]) -> None:
    buf.read_int()


def _s_dye_color(buf: PacketBuffer, protocol: int, skip_nbt: Optional[SkipNbt]) -> None:
    """dyed_color：Int 颜色 + Bool 显示提示。"""
    buf.read_int()
    buf.read_bool()


def _s_string(buf: PacketBuffer, protocol: int, skip_nbt: Optional[SkipNbt]) -> None:
    buf.read_string()


def _s_lore(buf: PacketBuffer, protocol: int, skip_nbt: Optional[SkipNbt]) -> None:
    for _ in range(_count(buf)):
        _read_nbt(buf, skip_nbt)


def _s_enchantments(buf: PacketBuffer, protocol: int, skip_nbt: Optional[SkipNbt]) -> None:
    """附魔/储魔：List<{VarInt 附魔 id, VarInt 等级}> + Bool 显示提示。"""
    for _ in range(_count(buf)):
        buf.read_varint()
        buf.read_varint()
    buf.read_bool()


def _s_adventure_predicate(buf: PacketBuffer, protocol: int, skip_nbt: Optional[SkipNbt]) -> None:
    """can_place_on / can_break：List<BlockPredicate> + Bool。"""
    for _ in range(_count(buf)):
        _sub_block_predicate(buf, skip_nbt)
    buf.read_bool()


def _make_attribute_modifiers(legacy: bool) -> SkipFn:
    attr_fn = _sub_attribute_legacy if legacy else _sub_attribute_121

    def _s(buf: PacketBuffer, protocol: int, skip_nbt: Optional[SkipNbt]) -> None:
        for _ in range(_count(buf)):
            attr_fn(buf)
        buf.read_bool()

    return _s


def _s_custom_model_data_legacy(buf: PacketBuffer, protocol: int, skip_nbt: Optional[SkipNbt]) -> None:
    buf.read_varint()


def _s_custom_model_data_new(buf: PacketBuffer, protocol: int, skip_nbt: Optional[SkipNbt]) -> None:
    """1.21.4+ 新格式：List<Float> + List<Bool> + List<String> + List<Int>。"""
    for _ in range(_count(buf)):
        buf.read_float()
    for _ in range(_count(buf)):
        buf.read_bool()
    for _ in range(_count(buf)):
        buf.read_string()
    for _ in range(_count(buf)):
        buf.read_int()


def _s_food_legacy(buf: PacketBuffer, protocol: int, skip_nbt: Optional[SkipNbt]) -> None:
    """1.20.6/1.21 Food：VarInt 营养; Float 饱和; Bool; Float 进食秒; List<Effect>。"""
    buf.read_varint()
    buf.read_float()
    buf.read_bool()
    buf.read_float()
    for _ in range(_count(buf)):
        _sub_effect(buf)


def _s_food_1212(buf: PacketBuffer, protocol: int, skip_nbt: Optional[SkipNbt]) -> None:
    """1.21.2+ Food：VarInt 营养; Float 饱和; Bool（无进食秒/效果）。"""
    buf.read_varint()
    buf.read_float()
    buf.read_bool()


def _s_tool(buf: PacketBuffer, protocol: int, skip_nbt: Optional[SkipNbt]) -> None:
    for _ in range(_count(buf)):
        _sub_rule(buf)
    buf.read_float()
    buf.read_varint()


def _s_slot_list(buf: PacketBuffer, protocol: int, skip_nbt: Optional[SkipNbt]) -> None:
    for _ in range(_count(buf)):
        _skip_slot(buf, protocol, skip_nbt)


def _s_potion_contents_legacy(buf: PacketBuffer, protocol: int, skip_nbt: Optional[SkipNbt]) -> None:
    if buf.read_bool():
        buf.read_varint()
    if buf.read_bool():
        buf.read_int()
    for _ in range(_count(buf)):
        _sub_potion_effect(buf)


def _s_potion_contents_1212(buf: PacketBuffer, protocol: int, skip_nbt: Optional[SkipNbt]) -> None:
    if buf.read_bool():
        buf.read_varint()
    if buf.read_bool():
        buf.read_int()
    for _ in range(_count(buf)):
        _sub_potion_effect(buf)
    if buf.read_bool():
        buf.read_string()


def _s_suspicious_stew(buf: PacketBuffer, protocol: int, skip_nbt: Optional[SkipNbt]) -> None:
    for _ in range(_count(buf)):
        buf.read_varint()
        buf.read_varint()


def _s_writable_book(buf: PacketBuffer, protocol: int, skip_nbt: Optional[SkipNbt]) -> None:
    for _ in range(_count(buf)):
        buf.read_string()
        if buf.read_bool():
            buf.read_string()


def _read_book_page(buf: PacketBuffer, skip_nbt: Optional[SkipNbt]) -> None:
    """书页内容：先尝试按 NBT 读，失败回退为 String（Hypixel 会发字符串页）。"""
    saved = buf.offset
    try:
        _read_nbt(buf, skip_nbt)
        return
    except (PacketReadError, ValueError):
        buf.offset = saved  # 回退游标改按 String 读
    buf.read_string()


def _s_written_book(buf: PacketBuffer, protocol: int, skip_nbt: Optional[SkipNbt]) -> None:
    buf.read_string()
    if buf.read_bool():
        buf.read_string()
    buf.read_string()
    buf.read_varint()
    for _ in range(_count(buf)):
        _read_book_page(buf, skip_nbt)
        if buf.read_bool():
            _read_book_page(buf, skip_nbt)
    buf.read_bool()


def _s_trim(buf: PacketBuffer, protocol: int, skip_nbt: Optional[SkipNbt]) -> None:
    """盔甲纹饰：material/pattern 均为 VarInt，==0 时内联数据。"""
    if buf.read_varint() == 0:
        buf.read_string()
        buf.read_varint()
        buf.read_float()
        n = _count(buf)
        for _ in range(n):
            buf.read_varint()
            buf.read_string()
        _read_nbt(buf, skip_nbt)
    if buf.read_varint() == 0:
        buf.read_string()
        buf.read_varint()
        _read_nbt(buf, skip_nbt)
        buf.read_bool()
    buf.read_bool()


def _s_instrument(buf: PacketBuffer, protocol: int, skip_nbt: Optional[SkipNbt]) -> None:
    if buf.read_varint() == 0:
        _sound_event(buf)
        buf.read_varint()
        buf.read_float()


def _s_lodestone_tracker(buf: PacketBuffer, protocol: int, skip_nbt: Optional[SkipNbt]) -> None:
    if buf.read_bool():
        buf.read_string()
        buf.read_bytes(8)  # Position（Long 编码坐标）
    buf.read_bool()


def _s_firework_explosion(buf: PacketBuffer, protocol: int, skip_nbt: Optional[SkipNbt]) -> None:
    """单个爆炸（无数量前缀）。"""
    _sub_firework_explosion(buf)


def _s_fireworks(buf: PacketBuffer, protocol: int, skip_nbt: Optional[SkipNbt]) -> None:
    buf.read_varint()
    for _ in range(_count(buf)):
        _sub_firework_explosion(buf)


def _s_profile(buf: PacketBuffer, protocol: int, skip_nbt: Optional[SkipNbt]) -> None:
    """玩家档案（1.21.9 之前的 legacy 格式，本项目三版本全部命中）。"""
    if buf.read_bool():
        buf.read_string()
    if buf.read_bool():
        buf.read_bytes(16)
    for _ in range(_count(buf)):
        buf.read_string()
        buf.read_string()
        if buf.read_bool():
            buf.read_string()


def _s_banner_patterns(buf: PacketBuffer, protocol: int, skip_nbt: Optional[SkipNbt]) -> None:
    for _ in range(_count(buf)):
        if buf.read_varint() == 0:
            buf.read_string()
            buf.read_string()
        buf.read_varint()


def _s_pot_decorations(buf: PacketBuffer, protocol: int, skip_nbt: Optional[SkipNbt]) -> None:
    for _ in range(_count(buf)):
        buf.read_varint()


def _s_slot_list_named(buf: PacketBuffer, protocol: int, skip_nbt: Optional[SkipNbt]) -> None:
    _s_slot_list(buf, protocol, skip_nbt)


def _s_block_state(buf: PacketBuffer, protocol: int, skip_nbt: Optional[SkipNbt]) -> None:
    for _ in range(_count(buf)):
        buf.read_string()
        buf.read_string()


def _s_bees(buf: PacketBuffer, protocol: int, skip_nbt: Optional[SkipNbt]) -> None:
    for _ in range(_count(buf)):
        _read_nbt(buf, skip_nbt)
        buf.read_varint()
        buf.read_varint()


def _s_jukebox_playable(buf: PacketBuffer, protocol: int, skip_nbt: Optional[SkipNbt]) -> None:
    if buf.read_bool():
        if buf.read_varint() == 0:
            _sound_event(buf)
            _read_nbt(buf, skip_nbt)
            buf.read_float()
            buf.read_varint()
    else:
        buf.read_string()
    buf.read_bool()


# ---- 1.21.2+ 新增组件 ----


def _s_item_model(buf: PacketBuffer, protocol: int, skip_nbt: Optional[SkipNbt]) -> None:
    buf.read_string()


def _s_use_remainder(buf: PacketBuffer, protocol: int, skip_nbt: Optional[SkipNbt]) -> None:
    _skip_slot(buf, protocol, skip_nbt)


def _s_use_cooldown(buf: PacketBuffer, protocol: int, skip_nbt: Optional[SkipNbt]) -> None:
    buf.read_float()
    if buf.read_bool():
        buf.read_string()


def _s_damage_resistant(buf: PacketBuffer, protocol: int, skip_nbt: Optional[SkipNbt]) -> None:
    buf.read_string()


def _s_equippable(buf: PacketBuffer, protocol: int, skip_nbt: Optional[SkipNbt]) -> None:
    buf.read_varint()
    _sound_event(buf)
    if buf.read_bool():
        buf.read_string()
    if buf.read_bool():
        buf.read_string()
    if buf.read_bool():
        _holder_set(buf)
    buf.read_bool()
    buf.read_bool()
    buf.read_bool()


def _s_repairable(buf: PacketBuffer, protocol: int, skip_nbt: Optional[SkipNbt]) -> None:
    _holder_set(buf)


def _s_tooltip_style(buf: PacketBuffer, protocol: int, skip_nbt: Optional[SkipNbt]) -> None:
    buf.read_string()


def _consume_effect(buf: PacketBuffer, protocol: int, skip_nbt: Optional[SkipNbt]) -> None:
    """ConsumeEffect（consumable/death_protection 共用）：按类型 id 分 5 种载荷。"""
    kind = buf.read_varint()
    if kind == 0:  # apply_effects
        for _ in range(_count(buf)):
            _sub_potion_effect(buf)
        buf.read_float()
    elif kind == 1:  # remove_effects
        _holder_set(buf)
    elif kind == 2:  # clear_all_effects
        pass
    elif kind == 3:  # teleport_randomly
        buf.read_float()
    elif kind == 4:  # play_sound
        _sound_event(buf)
    else:
        raise PacketReadError(f"未知 ConsumeEffect 类型 {kind}")


def _s_consumable(buf: PacketBuffer, protocol: int, skip_nbt: Optional[SkipNbt]) -> None:
    buf.read_float()
    buf.read_varint()
    _sound_event(buf)
    buf.read_bool()
    for _ in range(_count(buf)):
        _consume_effect(buf, protocol, skip_nbt)


def _s_death_protection(buf: PacketBuffer, protocol: int, skip_nbt: Optional[SkipNbt]) -> None:
    for _ in range(_count(buf)):
        _consume_effect(buf, protocol, skip_nbt)


# ---------------------------------------------------------------------------
# 注册表构建
# ---------------------------------------------------------------------------

# 1206 版 Attribute 子组件含 UUID；1.21+ 无
_ATTR_LEGACY = _make_attribute_modifiers(legacy=True)
_ATTR_121 = _make_attribute_modifiers(legacy=False)

# 1206 注册表（766 用）：id 0..55 按序
_REG_1206 = [
    _s_nbt,                  # 0 custom_data
    _s_varint,               # 1 max_stack_size
    _s_varint,               # 2 max_damage
    _s_varint,               # 3 damage
    _s_bool,                 # 4 unbreakable
    _s_nbt,                  # 5 custom_name
    _s_nbt,                  # 6 item_name
    _s_lore,                 # 7 lore
    _s_varint,               # 8 rarity
    _s_enchantments,         # 9 enchantments
    _s_adventure_predicate,  # 10 can_place_on
    _s_adventure_predicate,  # 11 can_break
    _ATTR_LEGACY,            # 12 attribute_modifiers
    _s_custom_model_data_legacy,  # 13 custom_model_data
    _s_empty,                # 14 hide_additional_tooltip
    _s_empty,                # 15 hide_tooltip
    _s_varint,               # 16 repair_cost
    _s_empty,                # 17 creative_slot_lock
    _s_bool,                 # 18 enchantment_glint_override
    _s_empty,                # 19 intangible_projectile
    _s_food_legacy,          # 20 food
    _s_empty,                # 21 fire_resistant
    _s_tool,                 # 22 tool
    _s_enchantments,         # 23 stored_enchantments
    _s_dye_color,            # 24 dyed_color (Int + Bool)
    _s_int,                  # 25 map_color (Int)
    _s_varint,               # 26 map_id
    _s_nbt,                  # 27 map_decorations
    _s_varint,               # 28 map_post_processing
    _s_slot_list,            # 29 charged_projectiles
    _s_slot_list,            # 30 bundle_contents
    _s_potion_contents_legacy,  # 31 potion_contents
    _s_suspicious_stew,      # 32 suspicious_stew_effects
    _s_writable_book,        # 33 writable_book_content
    _s_written_book,         # 34 written_book_content
    _s_trim,                 # 35 trim
    _s_nbt,                  # 36 debug_stick_state
    _s_nbt,                  # 37 entity_data
    _s_nbt,                  # 38 bucket_entity_data
    _s_nbt,                  # 39 block_entity_data
    _s_instrument,           # 40 instrument
    _s_varint,               # 41 ominous_bottle_amplifier
    _s_nbt,                  # 42 recipes
    _s_lodestone_tracker,    # 43 lodestone_tracker
    _s_firework_explosion,   # 44 firework_explosion
    _s_fireworks,            # 45 fireworks
    _s_profile,              # 46 profile
    _s_string,               # 47 note_block_sound
    _s_banner_patterns,      # 48 banner_patterns
    _s_varint,               # 49 base_color
    _s_pot_decorations,      # 50 pot_decorations
    _s_slot_list_named,      # 51 container
    _s_block_state,          # 52 block_state
    _s_bees,                 # 53 bees
    _s_nbt,                  # 54 lock
    _s_nbt,                  # 55 container_loot
]
assert len(_REG_1206) == 56

# 121 注册表（767 用）：在 42 处插入 jukebox_playable，之后整体 +1；
# 且 1.21 起 Attribute 子组件改为无 UUID 的 121 变体
_REG_121 = _REG_1206[:42] + [_s_jukebox_playable] + _REG_1206[42:]
_REG_121[12] = _ATTR_121
assert len(_REG_121) == 57

# 1212 注册表（769/1.21.4 用）：按 A3 顺序重组
_REG_1212 = [
    _REG_121[0],            # 0 custom_data
    _REG_121[1],            # 1 max_stack_size
    _REG_121[2],            # 2 max_damage
    _REG_121[3],            # 3 damage
    _REG_121[4],            # 4 unbreakable
    _REG_121[5],            # 5 custom_name
    _REG_121[6],            # 6 item_name
    _s_item_model,          # 7 item_model（新增）
    _REG_121[7],            # 8 lore
    _REG_121[8],            # 9 rarity
    _REG_121[9],            # 10 enchantments
    _REG_121[10],           # 11 can_place_on
    _REG_121[11],           # 12 can_break
    _ATTR_121,              # 13 attribute_modifiers（121 变体）
    _s_custom_model_data_new,  # 14 custom_model_data（769=1.21.4 新格式）
    _REG_121[14],           # 15 hide_additional_tooltip
    _REG_121[15],           # 16 hide_tooltip
    _REG_121[16],           # 17 repair_cost
    _REG_121[17],           # 18 creative_slot_lock
    _REG_121[18],           # 19 enchantment_glint_override
    _REG_121[19],           # 20 intangible_projectile
    _s_food_1212,           # 21 food（新格式）
    _s_consumable,          # 22 consumable（新增）
    _s_use_remainder,       # 23 use_remainder（新增）
    _s_use_cooldown,        # 24 use_cooldown（新增）
    _s_damage_resistant,    # 25 damage_resistant（新增）
    _REG_121[22],           # 26 tool
    _s_varint,              # 27 enchantable（新增）
    _s_equippable,          # 28 equippable（新增）
    _s_repairable,          # 29 repairable（新增）
    _s_empty,               # 30 glider（新增，0 字节）
    _s_tooltip_style,       # 31 tooltip_style（新增）
    _s_death_protection,    # 32 death_protection（新增）
    _REG_121[23],           # 33 stored_enchantments
    _REG_121[24],           # 34 dyed_color
    _REG_121[25],           # 35 map_color
    _REG_121[26],           # 36 map_id
    _REG_121[27],           # 37 map_decorations
    _REG_121[28],           # 38 map_post_processing
    _REG_121[29],           # 39 charged_projectiles
    _REG_121[30],           # 40 bundle_contents
    _s_potion_contents_1212,  # 41 potion_contents（末尾多 customName）
    _REG_121[32],           # 42 suspicious_stew_effects
    _REG_121[33],           # 43 writable_book_content
    _REG_121[34],           # 44 written_book_content
    _REG_121[35],           # 45 trim
    _REG_121[36],           # 46 debug_stick_state
    _REG_121[37],           # 47 entity_data
    _REG_121[38],           # 48 bucket_entity_data
    _REG_121[39],           # 49 block_entity_data
    _REG_121[40],           # 50 instrument
    _REG_121[41],           # 51 ominous_bottle_amplifier
    _s_jukebox_playable,    # 52 jukebox_playable
    _REG_121[43],           # 53 recipes
    _REG_121[44],           # 54 lodestone_tracker
    _REG_121[45],           # 55 firework_explosion
    _REG_121[46],           # 56 fireworks
    _REG_121[47],           # 57 profile
    _REG_121[48],           # 58 note_block_sound
    _REG_121[49],           # 59 banner_patterns
    _REG_121[50],           # 60 base_color
    _REG_121[51],           # 61 pot_decorations
    _REG_121[52],           # 62 container
    _REG_121[53],           # 63 block_state
    _REG_121[54],           # 64 bees
    _REG_121[55],           # 65 lock
    _REG_121[56],           # 66 container_loot
]
assert len(_REG_1212) == 67

# 协议版本 → 注册表（766=1.20.6、767=1.21、769=1.21.2-1.21.4）
_REGISTRIES: Dict[int, list] = {
    766: _REG_1206,
    767: _REG_121,
}


def _registry_for(protocol: int) -> list:
    if protocol >= 769:
        return _REG_1212
    reg = _REGISTRIES.get(protocol)
    if reg is None:
        raise PacketReadError(f"协议 {protocol} 无物品组件注册表")
    return reg


def skip_components(
    buf: PacketBuffer,
    protocol: int,
    count: int,
    skip_nbt: Optional[SkipNbt] = None,
) -> None:
    """按协议注册表逐个跳过 count 个组件。未知组件 ID 抛错，由调用方单包容错。"""
    table = _registry_for(protocol)
    for _ in range(count):
        cid = buf.read_varint()
        if cid < 0 or cid >= len(table):
            raise PacketReadError(f"未知组件类型 ID {cid}（协议 {protocol}）")
        table[cid](buf, protocol, skip_nbt)


def read_item_stack_modern(
    buf: PacketBuffer,
    protocol: int,
    skip_nbt: Optional[SkipNbt] = None,
) -> Optional[tuple]:
    """1.20.5+（766+）物品栈：count → item_id → 组件添加列表 → 移除列表。"""
    count = buf.read_varint()
    if count <= 0:
        return None
    item_id = buf.read_varint()
    add_n = buf.read_varint()
    if add_n > 0:
        skip_components(buf, protocol, add_n, skip_nbt)
    remove_n = buf.read_varint()
    for _ in range(max(remove_n, 0)):
        buf.read_varint()  # 移除的组件类型 ID 列表，可安全跳过
    return item_id, count
