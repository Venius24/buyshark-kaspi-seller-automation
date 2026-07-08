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
    def fetch_orders(page: Page, status: str) -> list:
        """
        Scrapes orders pending packaging or assembly.
        """
        # Go to orders page
        page.goto(f"https://kaspi.kz/mc/#/orders?status={status}")
        
        # Click packaging link if it exists
        try:
            page.get_by_role("link", name="Упаковка").click()
            page.wait_for_timeout(2000)
        except:
            pass
            
        try:
            page.wait_for_selector('tr, .order-item, .card, [role="row"]', timeout=8000)
        except Exception as err:
            print(f"Warning: orders wait timed out: {err}")
            
        if "login" in page.url:
            raise Exception("Сессия авторизации Kaspi истекла. Пожалуйста, выполните вход заново.")
            
        js_code = """
        () => {
            const orders = [];
            const elements = document.querySelectorAll('tr, .order-item, .card, [role="row"]');
            elements.forEach((el, index) => {
                const text = el.innerText || '';
                const idMatch = text.match(/\\b99\\d{7}\\b/);
                if (idMatch) {
                    const orderId = idMatch[0];
                    if (orders.some(o => o.id === orderId)) return;
                    
                    const lines = text.split('\\n').map(l => l.trim()).filter(Boolean);
                    
                    let deliveryType = 'Kaspi Доставка';
                    if (text.toLowerCase().includes('express')) deliveryType = 'Express';
                    else if (text.toLowerCase().includes('postomat')) deliveryType = 'Postomat';
                    
                    const timeMatch = text.match(/до \\d{2}:\\d{2}/);
                    const deadline = timeMatch ? timeMatch[0] : 'N/A';
                    
                    const priceMatch = text.match(/(\\d[\\d\\s]*)\\s*\\u20b8/);
                    const price = priceMatch ? priceMatch[0] : 'Цена не указана';
                    
                    // Filter product name line
                    const itemLines = lines.filter(line => {
                        const lower = line.toLowerCase();
                        if (lower.includes('заказ') || lower.includes('доставка') || lower.includes('express') || lower.includes('postomat') || lower.includes('выставить') || lower.includes('сформировать') || lower.includes('накладные')) return false;
                        if (line.includes('\\u20b8') || line.match(/\\b99\\d{7}\\b/) || line.match(/до \\d{2}:\\d{2}/) || line === '1' || line === '2' || line === '3') return false;
                        return true;
                    });
                    const productName = itemLines.length > 0 ? itemLines[0] : 'Товар';
                    
                    let qty = 1;
                    for (const line of lines) {
                        if (/^\\d+$/.test(line) && line !== orderId && parseInt(line) < 10) {
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
                        "status": text.includes('Сформировать') ? 'Assembling' : 'New'
                    });
                }
            });
            return orders;
        }
        """
        return page.evaluate(js_code)

    @staticmethod
    def pack_order(page: Page, order_id: str) -> bool:
        """
        Selects order checkbox by order_id and clicks 'Сформировать накладные'.
        """
        page.goto("https://kaspi.kz/mc/#/orders?status=KASPI_DELIVERY_CARGO_ASSEMBLY")
        page.wait_for_selector('tr, .order-item, .card, [role="row"]', timeout=8000)
        
        # Locate the specific row for this order
        row = page.locator('tr, .order-item, .card, [role="row"]', has_text=order_id)
        if not row.count():
            raise Exception(f"Заказ #{order_id} не найден на странице.")
            
        # Click the checkbox inside this row
        checkbox = row.locator('.b-checkbox, input[type="checkbox"]').first
        checkbox.click()
        page.wait_for_timeout(1000)
        
        # Click 'Сформировать накладные'
        pack_btn = page.get_by_role("button", name="Сформировать накладные")
        pack_btn.click()
        page.wait_for_timeout(4000)
        return True

    @staticmethod
    def fetch_transfers(page: Page, delivery_filter: str) -> list:
        """
        Navigates to Handover section and parses orders ready for transfer.
        """
        page.goto("https://kaspi.kz/mc/#/orders?status=KASPI_DELIVERY_CARGO_ASSEMBLY")
        page.wait_for_selector('a, link', timeout=8000)
        
        # Navigate to Handover (Передача)
        page.get_by_role("link", name=re.compile(r"Передача")).first.click()
        page.wait_for_timeout(3000)
        
        # Apply filter
        if delivery_filter == "ALL":
            page.locator("#ALL_DELIVERIES-label").click()
        elif delivery_filter == "EXPRESS":
            page.locator("#EXPRESS_DELIVERY-label").click()
        elif delivery_filter == "TODAY":
            page.locator("#TODAY_20-label").click()
        page.wait_for_timeout(2000)
        
        js_code = """
        () => {
            const orders = [];
            const elements = document.querySelectorAll('tr, .order-item, .card, [role="row"]');
            elements.forEach(el => {
                const text = el.innerText || '';
                const idMatch = text.match(/\\b99\\d{7}\\b/);
                if (idMatch) {
                    const orderId = idMatch[0];
                    if (orders.some(o => o.id === orderId)) return;
                    
                    const lines = text.split('\\n').map(l => l.trim()).filter(Boolean);
                    
                    let deliveryType = 'Kaspi Доставка';
                    if (text.toLowerCase().includes('express')) deliveryType = 'Express';
                    else if (text.toLowerCase().includes('postomat')) deliveryType = 'Postomat';
                    
                    const timeMatch = text.match(/до \\d{2}:\\d{2}/);
                    const deadline = timeMatch ? timeMatch[0] : 'N/A';
                    
                    let address = 'Алматы';
                    for (const line of lines) {
                        if (line.includes('улица') || line.includes('ул.') || line.includes('проспект') || line.startsWith('г.')) {
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
                }
            });
            return orders;
        }
        """
        return page.evaluate(js_code)
