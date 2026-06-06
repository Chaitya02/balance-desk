const CAT_ICONS = {
    'Eating Out':    `<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"><path d="M5 1v5a2 2 0 0 0 4 0V1M7 7v8M11 1c0 2.5 2 3.5 2 5.5 0 1.5-1 2.5-2 2.5v6"/></svg>`,
    'Groceries':     `<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M1.5 2.5H3l2 7h6.5l1.5-5H5.5"/><circle cx="6" cy="13" r="1"/><circle cx="10" cy="13" r="1"/></svg>`,
    'Transport':     `<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="3" width="12" height="9" rx="2"/><path d="M5 12v1.5M11 12v1.5M2 7h12"/></svg>`,
    'Rent':          `<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M1.5 7L8 2l6.5 5M3 7v6.5h4V10h2v3.5h4V7"/></svg>`,
    'Utilities':     `<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M9 1L5.5 8H10L7 15"/></svg>`,
    'Shopping':      `<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M5.5 6V4.5a2.5 2.5 0 0 1 5 0V6"/><path d="M2.5 6h11l-1 8h-9l-1-8z"/></svg>`,
    'Entertainment': `<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="8" r="6"/><path d="M6.5 5.5l4 2.5-4 2.5V5.5z" fill="currentColor" stroke="none"/></svg>`,
    'Health':        `<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M8 13.5C8 13.5 2 9.5 2 5.5a3 3 0 0 1 6-1 3 3 0 0 1 6 1c0 4-6 8-6 8z"/></svg>`,
    'Insurance':     `<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M8 1.5L2.5 4v4.5c0 3 2.5 5.5 5.5 6 3-.5 5.5-3 5.5-6V4L8 1.5z"/></svg>`,
    'Education':     `<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M8 2L1.5 5.5 8 9l6.5-3.5L8 2z"/><path d="M4 7v4.5c0 1.5 1.8 2.5 4 2.5s4-1 4-2.5V7M14.5 5.5v4"/></svg>`,
    'Subscriptions': `<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M13.5 8A5.5 5.5 0 1 1 8 2.5"/><path d="M10.5 1v3.5H14"/></svg>`,
    'Travel':        `<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><path d="M13.5 4.5l-8 2.5L4 5l-2 .5 1.5 2.5L2 10.5l2-.5.5-2L12 10l.5 2 2-.5-1-7z"/></svg>`,
    'Personal Care': `<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><circle cx="8" cy="6" r="3"/><path d="M3 14c0-2.76 2.24-5 5-5s5 2.24 5 5"/></svg>`,
    'Gifts':         `<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"><rect x="2" y="7" width="12" height="7" rx="1"/><path d="M1.5 5h13v2H1.5zM8 5V14M8 5c0 0-1-3.5 1.5-3.5S11 5 11 5M8 5c0 0 1-3.5-1.5-3.5S5 5 5 5"/></svg>`,
    'Miscellaneous': `<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"><circle cx="4.5" cy="4.5" r="1.5"/><circle cx="11.5" cy="4.5" r="1.5"/><circle cx="4.5" cy="11.5" r="1.5"/><circle cx="11.5" cy="11.5" r="1.5"/></svg>`,
};

const CAT_COLORS = {
    'Eating Out':    '#e67e22',
    'Groceries':     '#27ae60',
    'Transport':     '#2980b9',
    'Rent':          '#8e44ad',
    'Utilities':     '#f39c12',
    'Shopping':      '#c0392b',
    'Entertainment': '#e91e8c',
    'Health':        '#e74c3c',
    'Insurance':     '#16a085',
    'Education':     '#2c3e50',
    'Subscriptions': '#3498db',
    'Travel':        '#1abc9c',
    'Personal Care': '#9b59b6',
    'Gifts':         '#d35400',
    'Miscellaneous': '#7f8c8d',
};

const DEFAULT_CAT_ICON = `<svg width="16" height="16" viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.4" stroke-linecap="round"><path d="M2 5h12M2 8h8M2 11h5"/></svg>`;

function catIcon(label)  { return CAT_ICONS[label]  || DEFAULT_CAT_ICON; }
function catColor(label) { return CAT_COLORS[label] || '#999999'; }
