import re
import math
from playwright.sync_api import Page

class KaspiWrapper:
    @staticmethod
    def scrape_products(page: Page, status: str, page_num: int, search_query: str, page_size: int) -> dict:
        """
        Scrapes products from the Kaspi Merchant Cabinet by intercepting
        the bff/offer-view/list JSON API response.
        """
        # Determine start/end Kaspi pages (Kaspi shows 10 items per page)
        start_idx = (page_num - 1) * page_size
        end_idx = page_num * page_size
        kaspi_start_page = (start_idx // 10) + 1
        kaspi_end_page = ((end_idx - 1) // 10) + 1
        
        combined_products = []
        total_count = 0
        api_status = "true" if status == "active" else "false"
        
        for k_page in range(kaspi_start_page, kaspi_end_page + 1):
            url = f"https://kaspi.kz/mc/#/products?status={status}&page={k_page}"
            
            try:
                # Expect the products list API response
                with page.expect_response(lambda r: "bff/offer-view/list" in r.url and f"a={api_status}" in r.url, timeout=15000) as response_info:
                    page.goto(url, wait_until="domcontentloaded")
                
                response_data = response_info.value.json()
                total_count = response_data.get("total", 0)
                offers = response_data.get("data", [])
            except Exception as load_err:
                print(f"[KaspiWrapper] Initial API product load failed on page {k_page}: {load_err}")
                offers = []
            
            # If search is requested, enter search query in DOM and expect updated response
            if search_query and offers:
                try:
                    searchbox = page.get_by_role("searchbox", name="Артикул/Товар")
                    searchbox.click()
                    searchbox.fill(search_query)
                    
                    with page.expect_response(lambda r: "bff/offer-view/list" in r.url, timeout=10000) as search_response_info:
                        searchbox.press("Enter")
                    
                    response_data = search_response_info.value.json()
                    total_count = response_data.get("total", 0)
                    offers = response_data.get("data", [])
                except Exception as search_err:
                    print(f"[KaspiWrapper] API product search failed: {search_err}")
            
            # Map products from JSON
            for idx, item in enumerate(offers):
                master_sku = item.get("masterSku")
                product_url = f"https://kaspi.kz/shop/p/-{master_sku}/" if master_sku else ""
                
                combined_products.append({
                    "id": item.get("offerId", f"{k_page}_{idx}"),
                    "name": item.get("title", "Товар"),
                    "sku": item.get("sku", "N/A"),
                    "price": f"{item.get('price', 0)} ₸",
                    "status": "Активен" if item.get("available") else "Архив",
                    "url": product_url
                })
                
            print(f"[KaspiWrapper] Page {k_page}: scraped {len(offers)} items via API")
            
        if search_query:
            total_count = len(combined_products)
            
        offset = start_idx - (kaspi_start_page - 1) * 10
        sliced_products = combined_products[offset : offset + page_size] if combined_products else []
        total_pages = math.ceil(total_count / page_size) if total_count > 0 else 1
        
        print(f"[KaspiWrapper] Result: {len(sliced_products)} products, total={total_count}, pages={total_pages}")
        
        return {
            "products": sliced_products,
            "total_count": total_count,
            "total_pages": total_pages
        }

    @staticmethod
    def perform_product_action(page: Page, sku: str, action_type: str, payload) -> str:
        """
        Performs product actions (edit, archive, publish) in Kaspi Merchant Cabinet.
        """
        page.goto("https://kaspi.kz/mc/#/products")
        page.wait_for_selector('tr, .product-item, .card, [role="row"]', timeout=8000)
        
        clean_sku = "".join(c for c in sku if c.isalnum() or c == "-")
        searchbox = page.get_by_role("searchbox", name="Артикул/Товар")
        searchbox.click()
        searchbox.fill(clean_sku)
        searchbox.press("Enter")
        page.wait_for_timeout(3000)
        
        more_btn = page.get_by_role("img", name="more").first
        more_btn.click()
        page.wait_for_timeout(1000)
        
        if action_type == "edit":
            price, stock = payload
            page.get_by_role("listitem").filter(has_text="Изменить цену и остатки").click()
            page.wait_for_selector('[data-testid="stocks-modal-save"]', timeout=8000)
            
            price_input = page.get_by_role("textbox", name="Цена, ₸")
            price_input.click()
            price_input.press("Control+A")
            price_input.type(str(price))
            
            stock_input = page.get_by_role("textbox", name="Остатки от 0 до 10000 шт")
            stock_input.click()
            stock_input.press("Control+A")
            stock_input.type(str(stock))
            
            page.get_by_test_id("stocks-modal-save").click()
            page.wait_for_timeout(2000)
            
        elif action_type == "archive":
            page.get_by_role("listitem").filter(has_text="Снять с продажи").click()
            page.wait_for_timeout(2000)
            
        elif action_type == "publish":
            price, stock = payload
            page.get_by_role("listitem").filter(has_text="Выставить на продажу").click()
            page.wait_for_selector('[data-testid="stocks-modal-save"]', timeout=8000)
            
            stock_input = page.get_by_role("textbox", name="Остатки от 0 до 10000 шт")
            stock_input.click()
            stock_input.press("Control+A")
            stock_input.type(str(stock))
            
            page.get_by_test_id("stocks-modal-save").click()
            page.wait_for_timeout(2000)
            
        return f"Product {action_type} action completed successfully"

    @staticmethod
    def fetch_orders(page: Page, status: str, limit: int = 10) -> list:
        """
        Scrapes orders from Kaspi MC with multi-page pagination.
        Kaspi shows ~10 orders per page. This method will paginate through
        multiple pages to collect up to `limit` orders.
        """
        import datetime
        import math

        def log_msg(msg: str):
            timestamp = datetime.datetime.now().strftime("%H:%M:%S")
            print(f"[{timestamp}] {msg}", flush=True)

        total_pages_needed = math.ceil(limit / 10)
        log_msg(f"Запрос {limit} заказов (требуется страниц: {total_pages_needed})...")

        all_orders = []
        seen_ids = set()
        current_page_num = 1
        
        while current_page_num <= total_pages_needed:
            try:
                # Expect the GraphQL response
                with page.expect_response(
                    lambda r: "graphql" in r.url and "opName=getOrders" in r.url and r.request.method == "POST",
                    timeout=15000
                ) as response_info:
                    if current_page_num == 1:
                        url = f"https://kaspi.kz/mc/#/orders?status={status}"
                        page.goto(url, wait_until="domcontentloaded")
                    else:
                        clicked = False
                        for candidate_name in [f"Страница {current_page_num}.", f"Page {current_page_num}.", f"{current_page_num}"]:
                            try:
                                btn = page.get_by_role("button", name=candidate_name).first
                                if btn.is_visible():
                                    btn.click()
                                    clicked = True
                                    break
                            except Exception:
                                pass
                        
                        if not clicked:
                            for sel in [".pagination-next", ".next", ".arrow-right", "button.next", "[aria-label='Next page']"]:
                                try:
                                    btn = page.locator(sel).first
                                    if btn.is_visible():
                                        btn.click()
                                        clicked = True
                                        break
                                except Exception:
                                    pass
                                    
                        if not clicked:
                            raise Exception(f"Не удалось кликнуть кнопку перехода на страницу {current_page_num}")
                
                # Check for login redirection
                if "login" in page.url:
                    raise Exception("Сессия авторизации Kaspi истекла. Пожалуйста, выполните вход заново.")
                
                # Retrieve JSON
                response_data = response_info.value.json()
                
                # Navigate nested structure
                merchant = response_data.get("data", {}).get("merchant", {})
                orders_wrapper = merchant.get("orders", {}) if merchant else {}
                orders_dict = orders_wrapper.get("orders", {}) if orders_wrapper else {}
                orders_list = orders_dict.get("orders", []) if orders_dict else []
                
                # Dynamic page calculation based on real total count from first page
                if current_page_num == 1 and orders_dict:
                    total_orders = orders_dict.get("total", 0)
                    actual_pages_needed = math.ceil(total_orders / 10)
                    total_pages_needed = min(total_pages_needed, actual_pages_needed)
                    log_msg(f"Общее количество заказов: {total_orders}. Корректируем число страниц до: {total_pages_needed}")
            except Exception as load_err:
                log_msg(f"Ошибка загрузки страницы {current_page_num}: {load_err}")
                orders_list = []
                
            # If no orders returned on this page, we reached the end
            if not orders_list:
                break
                
            new_count = 0
            for order in orders_list:
                order_id = order.get("code")
                if not order_id:
                    continue
                    
                if order_id not in seen_ids:
                    seen_ids.add(order_id)
                    
                    # Parse deadline
                    planning_date = order.get("delivery", {}).get("transmissionPlanningDate")
                    if planning_date:
                        try:
                            time_part = planning_date.split('T')[1][:5]
                            deadline = f"до {time_part}"
                        except:
                            deadline = planning_date
                    else:
                        deadline = "N/A"
                        
                    # Parse deliveryType
                    is_express = order.get("delivery", {}).get("isExpress", False)
                    dest_type = order.get("destination", {}).get("__typename", "")
                    if is_express:
                        delivery_type = "Express"
                    elif dest_type == "Postomat":
                        delivery_type = "Postomat"
                    else:
                        delivery_type = "Kaspi Доставка"
                        
                    # Parse entries
                    entries = order.get("entries", [])
                    product_name = "Товар"
                    quantity = 1
                    if entries:
                        entry = entries[0]
                        product_name = entry.get("merchantProduct", {}).get("name") or entry.get("product", {}).get("name") or "Товар"
                        quantity = entry.get("quantity", 1)
                        
                    all_orders.append({
                        "id": order_id,
                        "deliveryType": delivery_type,
                        "deadline": deadline,
                        "price": f"{int(order.get('totalPrice', 0))} ₸",
                        "productName": product_name,
                        "quantity": quantity,
                        "status": "Assembling" if status == "KASPI_DELIVERY_CARGO_ASSEMBLY" else "New",
                        "pageNumber": current_page_num,
                        "_sourcePage": current_page_num
                    })
                    new_count += 1
                    
            log_msg(f"Стр. {current_page_num}/{total_pages_needed}: Загружено {len(orders_list)} заказов.")
            
            if len(all_orders) >= limit:
                break
                
            current_page_num += 1
            
        log_msg(f"Итого собрано: {len(all_orders)} заказов с {min(current_page_num, total_pages_needed)} страниц.")
        return all_orders[:limit]

    @staticmethod
    def pack_all_orders(page: Page) -> dict:
        """
        Navigates to CARGO_ASSEMBLY, selects ALL order checkboxes on the current page,
        and clicks 'Сформировать накладные' to bulk-transfer them.
        Uses the checkbox selector from codegen_sliced.py:
        .b-tooltip.is-dark.is-right > .tooltip-trigger > .b-checkbox > .check
        """
        page.goto("https://kaspi.kz/mc/#/orders?status=KASPI_DELIVERY_CARGO_ASSEMBLY", wait_until="domcontentloaded")
        
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        
        page.wait_for_timeout(3000)
        
        if "login" in page.url:
            raise Exception("Сессия авторизации Kaspi истекла. Пожалуйста, выполните вход заново.")
        
        # Wait for order content
        try:
            page.wait_for_selector('.cargo-assembly, table tbody tr', timeout=10000)
        except Exception as err:
            print(f"[KaspiWrapper] Pack all - selector timed out: {err}")
        
        # Find all checkboxes on the page and click each one
        checkboxes = page.locator('.b-tooltip.is-dark.is-right .tooltip-trigger .b-checkbox .check')
        checkbox_count = checkboxes.count()
        
        if checkbox_count == 0:
            # Fallback: try other checkbox selectors
            checkboxes = page.locator('.b-checkbox .check, input[type="checkbox"]')
            checkbox_count = checkboxes.count()
        
        if checkbox_count == 0:
            return {"packed": 0, "message": "Не найдено заказов для сборки на текущей странице."}
        
        print(f"[KaspiWrapper] Found {checkbox_count} checkboxes to click")
        
        # Click each checkbox
        clicked = 0
        for i in range(checkbox_count):
            try:
                cb = checkboxes.nth(i)
                if cb.is_visible():
                    cb.click()
                    clicked += 1
                    page.wait_for_timeout(300)  # Small delay between clicks
            except Exception as cb_err:
                print(f"[KaspiWrapper] Could not click checkbox {i}: {cb_err}")
        
        if clicked == 0:
            return {"packed": 0, "message": "Не удалось выбрать ни одного заказа."}
        
        page.wait_for_timeout(1000)
        
        # Click 'Сформировать накладные'
        try:
            pack_btn = page.get_by_role("button", name="Сформировать накладные")
            if pack_btn.is_visible(timeout=3000):
                pack_btn.click()
                page.wait_for_timeout(4000)
                print(f"[KaspiWrapper] Packed all {clicked} orders")
                return {"packed": clicked, "message": f"Успешно сформированы накладные для {clicked} заказов."}
            else:
                return {"packed": 0, "message": "Кнопка 'Сформировать накладные' не найдена."}
        except Exception as btn_err:
            print(f"[KaspiWrapper] Pack all button error: {btn_err}")
            return {"packed": 0, "message": f"Ошибка при нажатии кнопки: {str(btn_err)}"}

    @staticmethod
    def pack_order(page: Page, order_id: str, page_num: int = 1) -> bool:
        """
        Selects order checkbox by order_id and clicks 'Сформировать накладные'.
        Checkbox selector from codegen_sliced.py:
        .b-tooltip.is-dark.is-right > .tooltip-trigger > .b-checkbox > .check
        """
        page.goto(f"https://kaspi.kz/mc/#/orders?status=KASPI_DELIVERY_CARGO_ASSEMBLY&page={page_num}", wait_until="domcontentloaded")
        
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        
        page.wait_for_timeout(3000)
        
        if "login" in page.url:
            raise Exception("Сессия авторизации Kaspi истекла. Пожалуйста, выполните вход заново.")
        
        # Click the pagination button as a fallback if page_num > 1
        if page_num > 1:
            try:
                clicked = False
                for candidate_name in [f"Страница {page_num}.", f"Page {page_num}.", f"{page_num}"]:
                    btn = page.get_by_role("button", name=candidate_name).first
                    if btn.is_visible():
                        btn.click()
                        clicked = True
                        break
                if clicked:
                    page.wait_for_timeout(2000)
                    try:
                        page.wait_for_load_state("networkidle", timeout=5000)
                    except Exception:
                        pass
            except Exception as nav_err:
                print(f"[KaspiWrapper] Fallback pagination navigation error: {nav_err}")
        
        # Wait for order content
        try:
            page.wait_for_selector('.cargo-assembly, table tbody tr', timeout=10000)
        except Exception as err:
            print(f"[KaspiWrapper] Pack order selector timed out: {err}")
        
        # Find the row containing this order ID
        row = page.locator(f'div:has-text("{order_id}"), tr:has-text("{order_id}")').first
        row.wait_for(state="visible", timeout=5000)
            
        # Click the checkbox — use the exact selector from codegen_sliced.py
        try:
            checkbox = row.locator('.b-tooltip.is-dark.is-right .tooltip-trigger .b-checkbox .check').first
            if checkbox.is_visible():
                checkbox.click()
            else:
                # Fallback to any checkbox in the row
                checkbox = row.locator('.b-checkbox .check, input[type="checkbox"]').first
                checkbox.click()
        except Exception:
            # Last resort: try any checkbox-like element
            checkbox = row.locator('.check, input[type="checkbox"]').first
            checkbox.click()
        
        page.wait_for_timeout(1000)
        
        # Click 'Сформировать накладные'
        pack_btn = page.get_by_role("button", name="Сформировать накладные")
        pack_btn.click()
        page.wait_for_timeout(4000)
        
        print(f"[KaspiWrapper] Packed order {order_id} on page {page_num}")
        return True

    @staticmethod
    def fetch_transfers(page: Page, delivery_filter: str) -> list:
        """
        Navigates to Handover (Передача) section and parses orders ready for transfer.
        Navigation from codegen_sliced.py:
        - goto CARGO_ASSEMBLY page
        - click "Передача" link (may show count like "Передача 99+")
        - apply filter via radio labels: #ALL_DELIVERIES-label, #EXPRESS_DELIVERY-label, #TODAY_20-label
        """
        page.goto("https://kaspi.kz/mc/#/orders?status=KASPI_DELIVERY_CARGO_ASSEMBLY", wait_until="domcontentloaded")
        
        # Wait for SPA to render
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        
        page.wait_for_timeout(3000)
        
        if "login" in page.url:
            raise Exception("Сессия авторизации Kaspi истекла. Пожалуйста, выполните вход заново.")
        
        # Navigate to "Передача" tab — the link text may contain a count like "Передача 99+"
        try:
            # Try finding link that contains text "Передача" (partial match)
            transfer_link = page.locator('a:has-text("Передача")').first
            transfer_link.wait_for(state="visible", timeout=5000)
            transfer_link.click()
            page.wait_for_timeout(3000)
        except Exception as nav_err:
            print(f"[KaspiWrapper] Navigation to Передача failed: {nav_err}")
            # Fallback: try direct navigation
            page.goto("https://kaspi.kz/mc/#/orders?status=SIGN_REQUIRED", wait_until="domcontentloaded")
            page.wait_for_timeout(3000)
        
        # Apply delivery filter
        try:
            if delivery_filter == "ALL":
                filter_el = page.locator("#ALL_DELIVERIES-label")
                filter_el.wait_for(state="visible", timeout=3000)
                filter_el.click()
            elif delivery_filter == "EXPRESS":
                filter_el = page.locator("#EXPRESS_DELIVERY-label")
                filter_el.wait_for(state="visible", timeout=3000)
                filter_el.click()
            elif delivery_filter == "TODAY":
                filter_el = page.locator("#TODAY_20-label")
                filter_el.wait_for(state="visible", timeout=3000)
                filter_el.click()
            page.wait_for_timeout(2000)
        except Exception as filter_err:
            print(f"[KaspiWrapper] Filter application failed: {filter_err}")
        
        js_code = """
        () => {
            const orders = [];
            // Try multiple selectors for transfer rows
            let elements = document.querySelectorAll('.cargo-assembly > div, .transfer-row, .order-row');
            
            if (elements.length === 0) {
                elements = document.querySelectorAll('tr, .order-item, .card, [role="row"]');
            }
            
            elements.forEach(el => {
                const text = el.innerText || '';
                const idMatch = text.match(/\\b99\\d{7}\\b/);
                if (!idMatch) return;
                
                const orderId = idMatch[0];
                if (orders.some(o => o.id === orderId)) return;
                
                const lines = text.split('\\n').map(l => l.trim()).filter(Boolean);
                
                let deliveryType = 'Kaspi Доставка';
                if (text.toLowerCase().includes('express')) deliveryType = 'Express';
                else if (text.toLowerCase().includes('postomat')) deliveryType = 'Postomat';
                
                const timeMatch = text.match(/до \\d{2}:\\d{2}/);
                const deadline = timeMatch ? timeMatch[0] : 'N/A';
                
                // Find address line — contains street indicators
                let address = 'Алматы';
                for (const line of lines) {
                    if (line.includes('улица') || line.includes('ул.') || line.includes('проспект') || 
                        line.includes('пр.') || line.startsWith('г.') || line.includes('мкр') ||
                        line.includes('район') || line.includes('дом')) {
                        address = line;
                        break;
                    }
                }
                
                orders.push({
                    "id": orderId,
                    "deliveryType": deliveryType,
                    "deadline": deadline,
                    "address": address
                });
            });
            
            console.log('[KaspiWrapper] Scraped transfers:', orders.length);
            return orders;
        }
        """
        result = page.evaluate(js_code)
        print(f"[KaspiWrapper] Fetched {len(result)} transfers with filter={delivery_filter}")
        return result

