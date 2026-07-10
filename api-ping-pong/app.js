const API_URL = 'http://localhost:5000';

// DOM Elements
const authForm = document.getElementById('auth-form');
const storePhoneInput = document.getElementById('store-phone');
const apiTokenInput = document.getElementById('api-token');
const togglePwdBtn = document.getElementById('toggle-pwd');

const authStatusBox = document.getElementById('auth-status-box');
const statusDot = document.getElementById('status-dot');
const statusLabel = document.getElementById('status-label');

const btnPingCities = document.getElementById('btn-ping-cities');
const btnPingOrdersNew = document.getElementById('btn-ping-orders-new');
const btnPingOrdersAssembly = document.getElementById('btn-ping-orders-assembly');
const btnPingProducts = document.getElementById('btn-ping-products');
const btnPingTransfers = document.getElementById('btn-ping-transfers');

const consoleOutput = document.getElementById('console-output');
const responseStatus = document.getElementById('response-status');
const responseTimeLabel = document.getElementById('response-time');

// Toggle password visibility
togglePwdBtn.addEventListener('click', () => {
  const isPwd = apiTokenInput.type === 'password';
  apiTokenInput.type = isPwd ? 'text' : 'password';
  togglePwdBtn.textContent = isPwd ? '🔒' : '👁️';
});

// Check if there is a saved store in LocalStorage and verify session status on load
window.addEventListener('DOMContentLoaded', async () => {
  const savedPhone = localStorage.getItem('ping_pong_phone');
  if (savedPhone) {
    storePhoneInput.value = savedPhone;
    await checkSessionStatus(savedPhone);
  }
});

// Check session validity in the backend
async function checkSessionStatus(phone) {
  setLoadingState(true);
  try {
    const cleanPhone = phone.replace(/\D/g, '');
    const res = await fetch(`${API_URL}/api/verify-session`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ phone: cleanPhone })
    });
    const data = await res.json();
    
    if (res.ok && data.valid) {
      updateStatus(true, 'Сессия проверена: Активна');
      enableActionButtons(true);
      logConsole({ message: 'Сессия успешно восстановлена из кэша бэкенда.' }, 200, 0);
    } else {
      updateStatus(false, 'Сессия недействительна (введите токен)');
      enableActionButtons(false);
    }
  } catch (err) {
    updateStatus(false, 'Нет связи с сервером бэкенда');
    enableActionButtons(false);
    logConsole({ error: 'Не удалось подключиться к FastAPI бэкенду на порту 5000.' }, 'ERR', 0);
  } finally {
    setLoadingState(false);
  }
}

// Set visual loading state
function setLoadingState(loading) {
  const saveBtn = document.getElementById('btn-save-auth');
  if (loading) {
    saveBtn.disabled = true;
    saveBtn.querySelector('span').textContent = 'Проверка сессии...';
  } else {
    saveBtn.disabled = false;
    saveBtn.querySelector('span').textContent = 'Сохранить и проверить токен';
  }
}

// Update authentication status bar UI
function updateStatus(active, message) {
  if (active) {
    statusDot.className = 'status-indicator success';
    statusLabel.textContent = message;
    authStatusBox.style.borderColor = 'rgba(16, 185, 129, 0.3)';
    authStatusBox.style.background = 'rgba(16, 185, 129, 0.05)';
  } else {
    statusDot.className = 'status-indicator error';
    statusLabel.textContent = message;
    authStatusBox.style.borderColor = 'rgba(239, 68, 68, 0.3)';
    authStatusBox.style.background = 'rgba(239, 68, 68, 0.05)';
  }
}

// Enable or disable test buttons
function enableActionButtons(enable) {
  const btns = [btnPingCities, btnPingOrdersNew, btnPingOrdersAssembly, btnPingProducts, btnPingTransfers];
  btns.forEach(btn => btn.disabled = !enable);
}

// Form submit (Save & Verify Token)
authForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  setErrorMeta();
  
  const phone = storePhoneInput.value;
  const token = apiTokenInput.value.trim();
  const cleanPhone = phone.replace(/\D/g, '');
  
  if (cleanPhone.length !== 11) {
    logConsole({ error: 'Номер телефона должен состоять из 11 цифр.' }, 'ERR', 0);
    return;
  }
  
  logConsole({ status: 'Запуск верификации токена...', phone: cleanPhone }, 'WAIT', 0);
  setLoadingState(true);
  
  try {
    // 1. Start login process
    const startRes = await fetch(`${API_URL}/api/start-login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ phone: cleanPhone })
    });
    
    if (!startRes.ok) {
      const errData = await startRes.json();
      throw new Error(errData.detail || 'Ошибка инициализации сессии');
    }
    
    // 2. Submit API Token
    const startTime = performance.now();
    const submitRes = await fetch(`${API_URL}/api/submit-sms`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ phone: cleanPhone, sms: token })
    });
    const endTime = performance.now();
    const duration = Math.round(endTime - startTime);
    const submitData = await submitRes.json();
    
    if (submitRes.ok) {
      localStorage.setItem('ping_pong_phone', phone);
      updateStatus(true, 'Токен верифицирован');
      enableActionButtons(true);
      logConsole({
        success: true,
        message: 'Токен сохранен бэкендом и успешно подтвержден в Kaspi API.',
        backendResponse: submitData
      }, 200, duration);
    } else {
      updateStatus(false, 'Ошибка проверки токена');
      enableActionButtons(false);
      logConsole({
        error: 'Проверка токена не пройдена',
        details: submitData.detail
      }, submitRes.status, duration);
    }
  } catch (err) {
    updateStatus(false, 'Ошибка подключения');
    enableActionButtons(false);
    logConsole({
      error: 'Ошибка при отправке запроса авторизации.',
      message: err.message
    }, 'ERR', 0);
  } finally {
    setLoadingState(false);
  }
});

// Helper: Make API calls with performance tracking
async function runPingTest(endpoint, method = 'GET', body = null) {
  const phone = storePhoneInput.value;
  const cleanPhone = phone.replace(/\D/g, '');
  
  let url = `${API_URL}/api/${endpoint}`;
  if (method === 'GET') {
    const separator = url.includes('?') ? '&' : '?';
    url += `${separator}phone=${cleanPhone}`;
  }
  
  logConsole({ status: `Отправка запроса к ${method} ${url}...` }, 'PENDING', 0);
  
  const startTime = performance.now();
  try {
    const config = {
      method,
      headers: { 'Content-Type': 'application/json' }
    };
    if (body) {
      config.body = JSON.stringify(body);
    }
    
    const res = await fetch(url, config);
    const data = await res.json();
    const endTime = performance.now();
    const duration = Math.round(endTime - startTime);
    
    logConsole(data, res.status, duration);
  } catch (err) {
    const endTime = performance.now();
    const duration = Math.round(endTime - startTime);
    logConsole({
      error: 'Запрос к бэкенду завершился ошибкой',
      message: err.message
    }, 'ERR', duration);
  }
}

// Test Button Event Listeners
btnPingCities.addEventListener('click', () => {
  runPingTest('cities', 'GET');
});

btnPingOrdersNew.addEventListener('click', () => {
  runPingTest('orders?status=NEW&limit=5', 'GET');
});

btnPingOrdersAssembly.addEventListener('click', () => {
  runPingTest('orders?status=KASPI_DELIVERY_CARGO_ASSEMBLY&limit=5', 'GET');
});

btnPingProducts.addEventListener('click', () => {
  runPingTest('products?status=active&page=1&page_size=5', 'GET');
});

btnPingTransfers.addEventListener('click', () => {
  runPingTest('transfers?filter=ALL', 'GET');
});

// Console logger utility
function logConsole(jsonObj, statusCode, durationMs) {
  // Render JSON output
  consoleOutput.textContent = JSON.stringify(jsonObj, null, 2);
  
  // Update status meta code
  responseStatus.textContent = statusCode;
  responseStatus.className = 'meta-item code';
  if (statusCode === 200) {
    responseStatus.classList.add('success');
  } else if (statusCode === 'ERR' || (typeof statusCode === 'number' && statusCode >= 400)) {
    responseStatus.classList.add('error');
  }
  
  // Update duration
  responseTimeLabel.textContent = `${durationMs} ms`;
}

function setErrorMeta() {
  responseStatus.textContent = '—';
  responseStatus.className = 'meta-item code';
  responseTimeLabel.textContent = '— ms';
}
