import re

def _extract_order_ids(text: str) -> list[str]:
    return re.findall(r"ORD-\d+", text, re.IGNORECASE)

text = "I received my order ORD-1005 but the speakers are missing from the box! I need a refund."
print(f"Extracted: {_extract_order_ids(text)}")
