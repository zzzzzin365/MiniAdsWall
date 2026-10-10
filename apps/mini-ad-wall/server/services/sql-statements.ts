/** Ordinary DDL/DML files only. No DELIMITER directives or stored routine bodies. */
export function splitSqlStatements(source: string): string[] {
    let statement = '', quote = '', lineComment = false, blockComment = false;
    const result: string[] = [];
    for (let i = 0; i < source.length; i++) {
        const ch = source[i], next = source[i + 1];
        if (lineComment) { if (ch === '\n') { lineComment = false; statement += '\n'; } continue; }
        if (blockComment) { if (ch === '*' && next === '/') { blockComment = false; i++; statement += ' '; } continue; }
        if (quote) {
            statement += ch;
            if (ch === '\\' && next) { statement += next; i++; }
            else if (ch === quote) {
                if (next === quote) { statement += next; i++; } else quote = '';
            }
            continue;
        }
        if (ch === '-' && next === '-' && /\s/.test(source[i + 2] || ' ')) { lineComment = true; i++; statement += ' '; continue; }
        if (ch === '#') { lineComment = true; statement += ' '; continue; }
        if (ch === '/' && next === '*') {
            if (source[i + 2] === '!') throw new Error('executable_sql_comments_not_supported');
            blockComment = true; i++; statement += ' '; continue;
        }
        if (ch === "'" || ch === '"' || ch === '`') quote = ch;
        if (ch === ';') { if (statement.trim()) result.push(statement.trim()); statement = ''; }
        else statement += ch;
    }
    if (quote || blockComment) throw new Error('unterminated_sql_literal_or_comment');
    if (statement.trim()) result.push(statement.trim());
    if (result.some(sql => /^DELIMITER\b/i.test(sql))) throw new Error('sql_delimiter_not_supported');
    return result;
}
