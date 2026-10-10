/** Validate decimal precision before handing a bid to MySQL (including scientific notation). */
export function storedPrice(value: unknown): string | undefined {
    if (typeof value !== 'number' && typeof value !== 'string') return;
    const number = Number(value);
    if (!Number.isFinite(number) || number <= 0 || number >= 1e12) return;
    const parts = String(value).trim().match(/^\+?(\d*)(?:\.(\d*))?(?:e([+-]?\d+))?$/i);
    if (!parts || !(parts[1] + (parts[2] || ''))) return;
    const coefficient = parts[1] + (parts[2] || '');
    const trailingZeros = coefficient.match(/0*$/)[0].length;
    const decimals = Math.max(0, (parts[2] || '').length - Number(parts[3] || 0) - trailingZeros);
    if (decimals > 8) return;
    return number.toFixed(8);
}
