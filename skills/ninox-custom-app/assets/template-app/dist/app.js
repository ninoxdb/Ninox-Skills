import { createNinoxApp, isRateLimited } from './ninox-app-sdk.js';

const app = createNinoxApp();
const money = new Intl.NumberFormat(undefined, { currency: 'EUR', style: 'currency' });
const state = { items: [], selected: null };

const $ = (id) => document.getElementById(id);

function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' })[c]);
}

function errorText(error) {
    return isRateLimited(error) ? 'Too many requests. Wait a minute and try again.' : error.message;
}

// Reads every page of a table; list() returns at most 200 rows per call. Pass a handle made with a
// literal name, app.table('items'), so check_app.py can verify the table. The loop also stops when a
// page brings no new rows, so a server that ignores offset cannot make it spin forever.
async function listAll(table, options = {}) {
    const rows = new Map();
    for (let offset = 0; ; offset += 200) {
        const page = await table.list({ ...options, limit: 200, offset });
        const before = rows.size;
        for (const row of page.rows) rows.set(row.id, { id: row.id, ...row.values });
        if (!page.hasMore || rows.size === before) return [...rows.values()];
    }
}

function renderItems() {
    const category = $('category').value;
    const visible = state.items.filter((item) => !category || item.category === category);
    $('items').innerHTML = visible
        .map(
            (item) => `
            <article class="card">
                <div class="category">${escapeHtml(item.category)}</div>
                <h3>${escapeHtml(item.name)}</h3>
                <p>${escapeHtml(item.description)}</p>
                <div class="price">${money.format(Number(item.price) || 0)}</div>
                <a class="button" href="#/request/${encodeURIComponent(item.sku)}">Request</a>
            </article>`,
        )
        .join('');
}

function renderCategories() {
    const categories = [...new Set(state.items.map((item) => item.category).filter(Boolean))].sort();
    $('category').innerHTML =
        '<option value="">All</option>' + categories.map((c) => `<option>${escapeHtml(c)}</option>`).join('');
}

// Hash routing: the host serves real files only, so every view lives in index.html.
function route() {
    const match = location.hash.match(/^#\/request\/(.+)$/);
    const item = match ? state.items.find((i) => i.sku === decodeURIComponent(match[1])) : null;
    state.selected = item ?? null;
    $('view-catalog').hidden = item != null || location.hash === '#/done';
    $('view-request').hidden = item == null;
    $('view-done').hidden = location.hash !== '#/done';
    if (item) {
        $('request-title').textContent = `Request: ${item.name}`;
        $('request-status').textContent = '';
    }
}

async function submitRequest(event) {
    event.preventDefault();
    const form = event.target;
    if (!form.reportValidity()) return;
    const button = $('submit');
    button.disabled = true;
    $('request-status').textContent = 'Sending…';
    try {
        const requestNumber = `RQ-${Date.now().toString(36).toUpperCase()}`;
        await app.table('requests').create({
            request_number: requestNumber,
            item_sku: state.selected.sku,
            item_name: state.selected.name,
            quantity: Number(form.quantity.value),
            customer_name: form.customer_name.value.trim(),
            customer_email: form.customer_email.value.trim(),
            message: form.message.value.trim(),
        });
        form.reset();
        $('done-number').textContent = requestNumber;
        location.hash = '#/done';
    } catch (error) {
        $('request-status').textContent = `Couldn't send the request: ${errorText(error)}`;
    } finally {
        button.disabled = false;
    }
}

$('category').addEventListener('change', renderItems);
$('request-form').addEventListener('submit', submitRequest);
window.addEventListener('hashchange', route);

try {
    state.items = await listAll(app.table('items'), { sort: [{ field: 'name', direction: 'asc' }] });
    $('catalog-status').hidden = true;
    renderCategories();
    renderItems();
} catch (error) {
    $('catalog-status').textContent = `Couldn't load items: ${errorText(error)}`;
}
route();
