#!/usr/bin/env python3
"""
Fuzzy contact name resolver.
Usage: python resolve_name.py "<partner>"
Output: JSON {"id": N, "name": "...", "matched_variant": "...", "score": N}
        or    {"id": null, "name": null, "matched_variant": null, "score": 0} if no match above threshold
"""
import sys, json, sqlite3, io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

DB_PATH = 'D:/Kai/kai.db'
THRESHOLD = 60  # minimum score to consider a match

def get_contacts():
    conn = sqlite3.connect(DB_PATH)
    conn.text_factory = str
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT p.id, p.name, p.phone, GROUP_CONCAT(pn.nickname, '|||') as nicknames
        FROM people p
        LEFT JOIN people_nicknames pn ON p.id = pn.person_id
        GROUP BY p.id
    """).fetchall()
    conn.close()
    contacts = []
    for r in rows:
        names = [r['name']]
        if r['nicknames']:
            names += r['nicknames'].split('|||')
        contacts.append({'id': r['id'], 'canonical': r['name'], 'phone': r['phone'], 'variants': names})
    return contacts

def resolve(query: str):
    contacts = get_contacts()

    try:
        from rapidfuzz import fuzz
        score_fn = lambda a, b: fuzz.ratio(a, b)
    except ImportError:
        import difflib
        score_fn = lambda a, b: difflib.SequenceMatcher(None, a, b).ratio() * 100

    best_score = 0
    best_contact = None
    best_match = None

    for contact in contacts:
        for variant in contact['variants']:
            score = score_fn(query, variant)
            if score > best_score:
                best_score = score
                best_contact = contact
                best_match = variant

    if best_score >= THRESHOLD and best_contact:
        return {
            'id': best_contact['id'],
            'name': best_contact['canonical'],
            'phone': best_contact.get('phone'),
            'matched_variant': best_match,
            'score': round(best_score, 1)
        }

    # Fallback: search phone_contacts (imported VCF)
    parts = query.split()
    like_patterns = [f'%{p}%' for p in parts]
    conn = sqlite3.connect(DB_PATH)
    conn.text_factory = str
    conn.row_factory = sqlite3.Row

    # Prefer AND match (all parts present) over OR match (any part present)
    and_clauses = ' AND '.join(['name LIKE ?' for _ in like_patterns])
    rows_and = conn.execute(
        f"SELECT name, phone FROM phone_contacts WHERE {and_clauses} ORDER BY length(name) ASC",
        like_patterns
    ).fetchall()

    if not rows_and:
        # Fall back to OR — at least one part matches
        or_clauses = ' OR '.join(['name LIKE ?' for _ in like_patterns])
        rows_and = conn.execute(
            f"SELECT name, phone FROM phone_contacts WHERE {or_clauses} ORDER BY length(name) ASC",
            like_patterns
        ).fetchall()
    conn.close()

    if rows_and:
        # Score each result and pick best
        best_pc_score = 0
        best_pc_row = None
        for row in rows_and:
            score = score_fn(query, row['name'])
            if score > best_pc_score:
                best_pc_score = score
                best_pc_row = row
        if best_pc_row:
            return {
                'id': None,
                'name': best_pc_row['name'],
                'phone': best_pc_row['phone'],
                'matched_variant': best_pc_row['name'],
                'score': round(best_pc_score, 1)
            }

    return {'id': None, 'name': None, 'phone': None, 'matched_variant': None, 'score': round(best_score, 1)}

if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(json.dumps({'error': 'Usage: resolve_name.py <name>'}))
        sys.exit(1)
    query = sys.argv[1]
    result = resolve(query)
    print(json.dumps(result, ensure_ascii=False))
