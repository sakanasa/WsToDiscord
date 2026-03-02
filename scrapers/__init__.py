from __future__ import annotations
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ProductSnapshot:
    site: str           # "hobbystation" | "fukufuku"
    product_id: str     # 数字 ID 字串
    name: str           # 商品名称 (日文)
    price_int: int      # 日圆整数 (0 = 不明)
    price_raw: str      # 原始价格字串，如 "1,500"
    image_url: str      # 商品图片绝对 URL
    product_url: str    # 商品详细页面 URL
    in_stock: bool      # 是否有库存
    image_url_2: str = ""  # サムネ縮圖 URL（オプション）

    @property
    def state_key(self) -> str:
        return f"{self.site}:{self.product_id}"


@dataclass
class ChangeEvent:
    event_type: str              # "new" | "price_change" | "sold_out"
    snapshot: ProductSnapshot
    old_price_int: Optional[int] = None
    old_price_raw: Optional[str] = None
