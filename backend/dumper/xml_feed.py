from typing import List

def generate_xml_feed(merchant_id: str, products: List[dict]) -> bytes:
    """
    Генерирует XML-прайс-лист для Kaspi Catalog Sync.
    """
    from xml.etree.ElementTree import Element, SubElement, tostring
    import datetime

    now_str = datetime.datetime.now().strftime("%d.%m.%Y %H:%M:%S")

    root_el = Element("kaspi_catalog", {
        "date": now_str,
        "xmlns": "http://kaspi.kz/kaspicatalog",
        "xmlns:xsi": "http://www.w3.org/2001/XMLSchema-instance"
    })

    company_el = SubElement(root_el, "company")
    company_el.text = "ИП Твой Мерчант"

    merchant_el = SubElement(root_el, "merchantid")
    merchant_el.text = str(merchant_id)

    offers_el = SubElement(root_el, "offers")

    for p in products:
        sku = p.get("sku")
        if not sku:
            continue

        name = p.get("name") or f"Товар {sku}"
        brand = p.get("brand") or "Barkhan"

        price_num = p.get("price_num")
        if price_num is None:
            try:
                price_str = p.get("price", "0").replace(" ₸", "").replace(" ", "").strip()
                price_num = int(price_str)
            except Exception:
                price_num = 0

        stock = p.get("stock", 10)
        status = p.get("status", "Активен")
        available = "yes" if stock > 0 and status == "Активен" else "no"

        offer_el = SubElement(offers_el, "offer", {"sku": str(sku)})
        
        model_el = SubElement(offer_el, "model")
        model_el.text = str(name)

        brand_el = SubElement(offer_el, "brand")
        brand_el.text = str(brand)

        avail_parent = SubElement(offer_el, "availabilities")
        SubElement(avail_parent, "availability", {
            "available": available,
            "storeId": f"{merchant_id}_001",
            "stock": str(stock)
        })

        price_el = SubElement(offer_el, "price")
        price_el.text = str(price_num)

    xml_str = tostring(root_el, encoding="utf-8")
    return b'<?xml version="1.0" encoding="utf-8"?>\n' + xml_str
