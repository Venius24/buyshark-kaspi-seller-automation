import { Elysia, t } from 'elysia';
import { cors } from '@elysiajs/cors';
import { kaspiAutomation, getStateFilePath } from './automation/kaspi';
import * as fs from 'fs';

const app = new Elysia()
  .use(cors())
  .get('/', () => ({ status: 'Kaspi SaaS Automation API is running' }))
  
  .get('/api/status', ({ query }) => {
    const { phone } = query;
    if (!phone) {
      return { success: false, error: 'Phone parameter is required' };
    }
    const statePath = getStateFilePath(phone);
    const hasSession = fs.existsSync(statePath);
    return { success: true, has_session: hasSession };
  }, {
    query: t.Object({
      phone: t.String()
    })
  })

  .post('/api/start-login', async ({ body }) => {
    const { phone } = body;
    try {
      const msg = await kaspiAutomation.startLogin(phone);
      return { success: true, message: msg };
    } catch (error: any) {
      return { success: false, error: error.message };
    }
  }, {
    body: t.Object({
      phone: t.String()
    })
  })

  .post('/api/submit-sms', async ({ body }) => {
    const { phone, sms } = body;
    try {
      const msg = await kaspiAutomation.submitSms(phone, sms);
      return { success: true, message: msg };
    } catch (error: any) {
      return { success: false, error: error.message };
    }
  }, {
    body: t.Object({
      phone: t.String(),
      sms: t.String()
    })
  })

  .post('/api/verify-session', async ({ body }) => {
    const { phone } = body;
    try {
      const valid = await kaspiAutomation.verifySession(phone);
      return { success: true, valid };
    } catch (error: any) {
      return { success: false, error: error.message };
    }
  }, {
    body: t.Object({
      phone: t.String()
    })
  })

  .post('/api/close', async () => {
    await kaspiAutomation.cleanup();
    return { success: true, message: 'Cleaned up automation browser' };
  })

  .listen(5000);

console.log(`🦊 Elysia is running at ${app.server?.hostname}:${app.server?.port}`);
