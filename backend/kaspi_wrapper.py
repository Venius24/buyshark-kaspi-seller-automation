import re
import math
from playwright.sync_api import Page

class KaspiWrapper:
    @staticmethod
    def scrape_products(page: Page, status: str, page_num: int, search_query: str, page_size: int) -> dict:
        """
        Scrapes products from the Kaspi Merchant Cabinet.
        Navigation mirrors codegen3/codegen_sliced: goto MC orders page first,
        then click "Управление товарами" or navigate via hash URL and wait for
        the SPA to fully render the product list.
        """
        # Determine start/end Kaspi pages (Kaspi shows 10 items per page)
        start_idx = (page_num - 1) * page_size
        end_idx = page_num * page_size
        kaspi_start_page = (start_idx // 10) + 1
        kaspi_end_page = ((end_idx - 1) // 10) + 1
        
        combined_products = []
        scraped_total = None
        
        # Improved JS DOM Scraper — targets product list items more precisely
        js_code = """
        () => {
            const items = [];
            // Kaspi MC product page renders products in a list/table structure
            // Try multiple selectors that match Kaspi's actual DOM
            let elements = document.querySelectorAll('.product-list-item, .product-row, .b-table tbody tr');
            
            // Fallback: if specific selectors found nothing, try generic table rows
            if (elements.length === 0) {
                elements = document.querySelectorAll('table tbody tr, .products-table tr');
            }
            
            // Final fallback: any row-like element that contains price symbol
            if (elements.length === 0) {
                const allRows = document.querySelectorAll('tr, [role="row"], .card');
                elements = Array.from(allRows).filter(el => {
                    const text = el.innerText || '';
                    return text.includes('₸') && text.split('\\n').filter(Boolean).length >= 2;
                });
            }
            
            elements.forEach((el, index) => {
                const text = el.innerText || '';
                if (!text.includes('₸')) return;
                
                const lines = text.split('\\n').map(l => l.trim()).filter(Boolean);
                if (lines.length < 2) return;
                
                // Skip header rows
                if (text.includes('Артикул/Товар') && text.includes('Категория')) return;
                
                const priceMatch = text.match(/(\\d[\\d\\s]*)\\s*₸/);
                const price = priceMatch ? priceMatch[0] : 'Цена не указана';
                
                // Product name is typically the first meaningful line
                let name = lines[0];
                // If first line looks like just a number or checkbox, try next
                if (/^\\d+$/.test(name) || name.length < 3) {
                    name = lines.length > 1 ? lines[1] : name;
                }
                
                let sku = 'N/A';
                for (const line of lines) {
                    if (line.toLowerCase().includes('код') || line.toLowerCase().includes('sku')) {
                        sku = line;
                        break;
                    }
                }
                // Try matching alphanumeric/dash article codes (like D655-0006, S517-0015)
                if (sku === 'N/A') {
                    for (let i = 1; i < lines.length; i++) {
                        const line = lines[i];
                        if (/^[A-Za-z0-9][A-Za-z0-9_-]{3,24}$/.test(line) && !line.includes('₸')) {
                            sku = line;
                            break;
                        }
                    }
                }
                
                const linkEl = el.querySelector('a[href*="/shop/p/"], a[href*="products"]');
                const href = linkEl ? linkEl.href : '';
                
                items.push({
                    "id": String(index + 1),
                    "name": name,
                    "sku": sku,
                    "price": price,
                    "status": (text.includes('В архиве') || text.includes('Снят с продажи')) ? 'Архив' : 'Активен',
                    "url": href
                });
            });
            return items;
        }
        """
        
        for k_page in range(kaspi_start_page, kaspi_end_page + 1):
            url = f"https://kaspi.kz/mc/#/products?status={status}&page={k_page}"
            page.goto(url, wait_until="domcontentloaded")
            
            # Wait for SPA to render — use networkidle + extra timeout for JS rendering
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass
            
            # Additional wait for SPA content to fully render
            page.wait_for_timeout(3000)
            
            # Check if session expired (redirected to login)
            if "login" in page.url:
                raise Exception("Сессия авторизации Kaspi истекла. Пожалуйста, выполните вход заново.")
            
            # Try to wait for actual product content
            try:
                page.wait_for_selector('table tbody tr, .product-list-item, .product-row', timeout=10000)
            except Exception as wait_err:
                print(f"Warning: product selector timed out on page {k_page}: {wait_err}")
                # Try clicking "Управление товарами" link as fallback (SPA navigation)
                try:
                    link = page.get_by_role("link", name="Управление товарами")
                    if link.is_visible():
                        link.click()
                        page.wait_for_timeout(3000)
                except Exception:
                    pass
            
            if search_query:
                try:
                    searchbox = page.get_by_role("searchbox", name="Артикул/Товар")
                    searchbox.click()
                    searchbox.fill(search_query)
                    searchbox.press("Enter")
                    page.wait_for_timeout(3000)
                except Exception as search_err:
                    print(f"Search automation failed on page {k_page}: {search_err}")
            
            # Scrape total items count from tab text: "В продаже (904)" or "Сняты с продажи (478)"
            if scraped_total is None:
                try:
                    tab_text = page.evaluate("() => document.body.innerText")
                    if status == "active":
                        match = re.search(r"В продаже\s*\((\d+)\)", tab_text)
                    elif status == "archive":
                        match = re.search(r"Сняты с продажи\s*\((\d+)\)", tab_text)
                    else:
                        match = re.search(r"\((\d+)\)", tab_text)
                    if match:
                        scraped_total = int(match.group(1))
                        print(f"[KaspiWrapper] Found total count: {scraped_total} for status={status}")
                except Exception as count_err:
                    print(f"[KaspiWrapper] Could not parse total count: {count_err}")
                    
            page_items = page.evaluate(js_code)
            print(f"[KaspiWrapper] Page {k_page}: scraped {len(page_items)} items")
            if page_items:
                combined_products.extend(page_items)
                
        if search_query:
            total_count = len(combined_products)
        else:
            total_count = scraped_total if scraped_total is not None else len(combined_products)
            
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
        page.goto(f"https://kaspi.kz/mc/#/orders?status={status}", wait_until="domcontentloaded")
        
        # Wait for SPA to render
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except Exception:
            pass
        
        page.wait_for_timeout(3000)
        
        if "login" in page.url:
            raise Exception("Сессия авторизации Kaspi истекла. Пожалуйста, выполните вход заново.")
        
        # Click "Упаковка" link if it exists (navigates to packaging sub-section)
        try:
            packaging_link = page.get_by_role("link", name="Упаковка")
            packaging_link.wait_for(state="visible", timeout=3000)
            packaging_link.click()
            page.wait_for_timeout(3000)
        except Exception:
            pass
        
        # Wait for order content to appear
        try:
            page.wait_for_selector('.cargo-assembly, .order-list, table tbody tr, [role="row"]', timeout=10000)
        except Exception as err:
            print(f"[KaspiWrapper] Orders selector timed out: {err}")
            
        js_code = """
        (pageNum) => {
            const orders = [];
            // Try cargo-assembly divs first (from codegen_sliced), then fallback to rows
            let elements = document.querySelectorAll('.cargo-assembly > div, .order-row');
            
            if (elements.length === 0) {
                elements = document.querySelectorAll('tr, .order-item, .card, [role="row"]');
            }
            
            elements.forEach((el, index) => {
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
                
                const priceMatch = text.match(/(\\d[\\d\\s]*)\\s*₸/);
                const price = priceMatch ? priceMatch[0] : 'Цена не указана';
                
                // Filter out non-product lines to find the product name
                const itemLines = lines.filter(line => {
                    const lower = line.toLowerCase();
                    if (lower.includes('заказ') || lower.includes('доставка') || lower.includes('express') || lower.includes('postomat') || lower.includes('выставить') || lower.includes('сформировать') || lower.includes('накладные') || lower.includes('упаковка') || lower.includes('передача')) return false;
                    if (line.includes('₸') || /\\b99\\d{7}\\b/.test(line) || /до \\d{2}:\\d{2}/.test(line) || /^\\d{1,2}$/.test(line)) return false;
                    return line.length > 3;
                });
                const productName = itemLines.length > 0 ? itemLines[0] : 'Товар';
                
                // Find quantity (single digit number that isn't the order ID)
                let qty = 1;
                for (const line of lines) {
                    if (/^\\d+$/.test(line) && line !== orderId && parseInt(line) < 100 && parseInt(line) > 0) {
                        qty = parseInt(line);
                        break;
                    }
                }
                
                orders.push({
                    "id": orderId,
                    "deliveryType": deliveryType,
                    "deadline": deadline,
                    "price": price,
                    "productName": productName,
                    "quantity": qty,
                    "status": text.includes('Сформировать') ? 'Assembling' : 'New',
                    "pageNumber": pageNum,
                    "_sourcePage": pageNum
                });
            });
            
            return orders;
        }
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
            # Scrape current page
            page_orders = page.evaluate(js_code, current_page_num)
            
            new_count = 0
            for order in page_orders:
                if order["id"] not in seen_ids:
                    seen_ids.add(order["id"])
                    all_orders.append(order)
                    new_count += 1
            
            log_msg(f"Стр. {current_page_num}/{total_pages_needed}: Загружено {len(page_orders)} заказов.")
            
            # If we already have enough orders, or if this page returned 0 orders, we can stop
            if len(all_orders) >= limit:
                break
                
            if len(page_orders) == 0:
                break

            # Try to navigate to the next page
            if current_page_num < total_pages_needed:
                next_page_num = current_page_num + 1
                clicked = False
                
                # List of possible ways to click the next page button
                try:
                    btn = page.get_by_role("button", name=f"Страница {next_page_num}.").first
                    btn.wait_for(state="visible", timeout=1000)
                    btn.click()
                    clicked = True
                except Exception:
                    pass
                    
                if not clicked:
                    try:
                        btn = page.get_by_role("button", name=f"Page {next_page_num}.").first
                        btn.wait_for(state="visible", timeout=1000)
                        btn.click()
                        clicked = True
                    except Exception:
                        pass
                        
                if not clicked:
                    try:
                        btn = page.get_by_role("button", name=f"{next_page_num}").first
                        btn.wait_for(state="visible", timeout=1000)
                        btn.click()
                        clicked = True
                    except Exception:
                        pass
                        
                if not clicked:
                    try:
                        btn = page.get_by_role("button", name="Next page").first
                        btn.wait_for(state="visible", timeout=1000)
                        btn.click()
                        clicked = True
                    except Exception:
                        pass
                        
                if not clicked:
                    # Try class-based or aria-based selectors
                    for sel in [".pagination-next", ".next", ".arrow-right", "button.next", "[aria-label='Next page']", "[aria-label='Следующая страница']"]:
                        try:
                            btn = page.locator(sel).first
                            btn.wait_for(state="visible", timeout=500)
                            btn.click()
                            clicked = True
                            break
                        except Exception:
                            pass
                
                if not clicked:
                    log_msg(f"Не удалось найти кнопку перехода на страницу {next_page_num}. Завершаем пагинацию.")
                    break
                
                # Wait for navigation/render
                page.wait_for_timeout(2000)
                try:
                    page.wait_for_load_state("networkidle", timeout=5000)
                except Exception:
                    pass
                page.wait_for_timeout(1000)
                
            current_page_num += 1
            
        log_msg(f"Итого собрано: {len(all_orders)} заказов с {min(current_page_num, total_pages_needed)} страниц.")
        
        # Trim to requested limit
        result = all_orders[:limit]
        return result

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

