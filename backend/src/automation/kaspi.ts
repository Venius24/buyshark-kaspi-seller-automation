import { chromium, type Browser, type BrowserContext, type Page } from 'playwright';
import * as fs from 'fs';
import * as path from 'path';

const STATE_DIR = path.join(process.cwd(), 'states');
if (!fs.existsSync(STATE_DIR)) {
  fs.mkdirSync(STATE_DIR, { recursive: true });
}

export function getStateFilePath(phone: string): string {
  const sanitizedPhone = phone.replace(/[^0-9]/g, '');
  return path.join(STATE_DIR, `state_${sanitizedPhone}.json`);
}

class KaspiAutomation {
  private browser: Browser | null = null;
  private context: BrowserContext | null = null;
  private page: Page | null = null;

  async startLogin(phone: string): Promise<string> {
    await this.cleanup();

    // Launch Chromium headful for verification, but can be headless in prod
    this.browser = await chromium.launch({ headless: false });
    this.context = await this.browser.newContext();
    this.page = await this.context.newPage();

    await this.page.goto('https://idmc.shop.kaspi.kz/login');
    try {
      await this.page.getByRole('tab', { name: 'Телефон' }).click();
    } catch (e) {}

    try {
      await this.page.getByPlaceholder('Телефон').click();
      await this.page.getByPlaceholder('Телефон').fill(phone);
    } catch (e) {
      await this.page.getByRole('textbox', { name: '+7 ( ) - -' }).click();
      await this.page.getByRole('textbox', { name: '+7 ( ) - -' }).fill(phone);
    }
    await this.page.getByRole('button', { name: 'Продолжить' }).click();

    // Short sleep for transition animations
    await this.page.waitForTimeout(1000);
    return 'SMS sent to phone';
  }

  async submitSms(phone: string, sms: string): Promise<string> {
    if (!this.page || !this.context) {
      throw new Error('Authorization session not started.');
    }

    try {
      await this.page.getByPlaceholder('Введите код').click();
      await this.page.getByPlaceholder('Введите код').fill(sms);
    } catch (e) {
      await this.page.getByRole('textbox', { name: 'Введите код' }).click();
      await this.page.getByRole('textbox', { name: 'Введите код' }).fill(sms);
    }
    await this.page.getByRole('button', { name: 'Продолжить' }).click();

    // Wait for potential redirects/navigation
    await this.page.waitForTimeout(4000);

    const currentUrl = this.page.url();
    if (currentUrl.includes('login')) {
      throw new Error('Login failed. Please check the SMS code.');
    }

    // Save session storage state for the specific phone number
    const statePath = getStateFilePath(phone);
    await this.context.storageState({ path: statePath });

    await this.cleanup();
    return 'Success';
  }

  async verifySession(phone: string): Promise<boolean> {
    const statePath = getStateFilePath(phone);
    if (!fs.existsSync(statePath)) {
      return false;
    }

    const tempBrowser = await chromium.launch({ headless: true });
    try {
      const tempContext = await tempBrowser.newContext({ storageState: statePath });
      const tempPage = await tempContext.newPage();
      await tempPage.goto('https://kaspi.kz/mc/#/orders?status=NEW');
      await tempPage.waitForTimeout(3000);

      const isValid = !tempPage.url().includes('login');
      await tempBrowser.close();
      return isValid;
    } catch (e) {
      await tempBrowser.close();
      return false;
    }
  }

  async cleanup(): Promise<void> {
    if (this.browser) {
      try {
        await this.browser.close();
      } catch (e) {
        // ignore
      }
      this.browser = null;
      this.context = null;
      this.page = null;
    }
  }
}

export const kaspiAutomation = new KaspiAutomation();
