#!/usr/bin/env python3
"""
Filter companies in the jobs database by score, timezone, domain, etc.

Usage:
  # Companies with no domain, score >= 5
  python3 filter_companies.py --no-domain --min-score 5

  # Companies with no domain, score between 6 and 8
  python3 filter_companies.py --no-domain --min-score 6 --max-score 8

  # Companies from a specific timezone, score >= 6
  python3 filter_companies.py --timezone "US/Eastern" --min-score 6

  # All qualified companies, top 20 by score
  python3 filter_companies.py --limit 20

  # Export as CSV
  python3 filter_companies.py --no-domain --min-score 5 --csv

  # Show summary stats
  python3 filter_companies.py --stats

  # Find career URLs for filtered results (dry-run mode)
  python3 filter_companies.py --no-domain --min-score 5 --find-urls
"""

import argparse
import csv
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path("/opt/job-agent/data/jobs.db")


def get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def build_query(args):
    conditions = []
    params = []

    # Qualification filter
    if args.qualified:
        conditions.append("c.qualified = 1")
    elif args.unqualified:
        conditions.append("(c.qualified IS NULL OR c.qualified = 0)")

    # Domain filter
    if args.no_domain:
        conditions.append("(c.domain IS NULL OR c.domain = '')")
    if args.has_domain:
        conditions.append("(c.domain IS NOT NULL AND c.domain != '')")

    # Score filter
    if args.min_score is not None:
        conditions.append("c.remote_score >= ?")
        params.append(args.min_score)
    if args.max_score is not None:
        conditions.append("c.remote_score <= ?")
        params.append(args.max_score)

    # Timezone filter
    if args.timezone:
        conditions.append("c.timezone = ?")
        params.append(args.timezone)
    if args.no_timezone:
        conditions.append("(c.timezone IS NULL OR c.timezone = '')")

    # Source filter
    if args.source:
        conditions.append("c.source = ?")
        params.append(args.source)

    # Search in name
    if args.search:
        conditions.append("c.name LIKE ?")
        params.append(f"%{args.search}%")

    # Has careers_url
    if args.has_url:
        conditions.append("(c.careers_url IS NOT NULL AND c.careers_url != '')")
    if args.no_url:
        conditions.append("(c.careers_url IS NULL OR c.careers_url = '')")

    # Applied filter
    if args.applied is not None:
        conditions.append("c.applied = ?")
        params.append(1 if args.applied else 0)

    where = ""
    if conditions:
        where = "WHERE " + " AND ".join(conditions)

    order = args.order if args.order else "c.remote_score DESC"

    return f"""
        SELECT c.*, 
               (SELECT COUNT(*) FROM contacts ct WHERE ct.company_id = c.id) AS contact_count,
               (SELECT COUNT(*) FROM emails e WHERE e.company_id = c.id) AS email_count,
               (SELECT GROUP_CONCAT(ct2.email) FROM contacts ct2 WHERE ct2.company_id = c.id) AS emails
        FROM companies c
        {where}
        ORDER BY {order}
    """, params


def show_stats(args):
    conn = get_conn()
    cur = conn.execute("""
        SELECT 
            COUNT(*) AS total,
            SUM(CASE WHEN qualified = 1 THEN 1 ELSE 0 END) AS qualified,
            SUM(CASE WHEN qualified = 0 THEN 1 ELSE 0 END) AS disqualified,
            SUM(CASE WHEN qualified IS NULL THEN 1 ELSE 0 END) AS pending,
            SUM(CASE WHEN domain IS NULL OR domain = '' THEN 1 ELSE 0 END) AS no_domain,
            SUM(CASE WHEN domain IS NOT NULL AND domain != '' THEN 1 ELSE 0 END) AS has_domain,
            ROUND(AVG(remote_score), 1) AS avg_score,
            MAX(remote_score) AS max_score,
            SUM(CASE WHEN timezone IS NOT NULL AND timezone != '' THEN 1 ELSE 0 END) AS has_timezone
        FROM companies
    """)
    row = dict(cur.fetchone())
    
    print("=" * 55)
    print(f"  📊 Pipeline Stats (total: {row['total']})")
    print("=" * 55)
    print(f"  ✅ Qualified:      {row['qualified']}")
    print(f"  ❌ Disqualified:   {row['disqualified']}")
    print(f"  ⏳ Pending:        {row['pending']}")
    print(f"  🌐 Has domain:     {row['has_domain']}")
    print(f"  🚫 No domain:      {row['no_domain']}")
    print(f"  ⏰ Has timezone:   {row['has_timezone']}")
    print(f"  📈 Avg score:      {row['avg_score']}")
    print(f"  🏆 Max score:      {row['max_score']}")
    print()
    
    # Score distribution
    print("  Score Distribution (qualified):")
    rows = conn.execute("""
        SELECT remote_score, COUNT(*) as cnt 
        FROM companies 
        WHERE qualified = 1 
        GROUP BY remote_score 
        ORDER BY remote_score DESC
    """).fetchall()
    for r in rows:
        bar = "█" * (r["cnt"] // 5) + ("▌" if r["cnt"] % 5 >= 3 else ("▎" if r["cnt"] % 5 >= 1 else ""))
        print(f"    {r['remote_score']:3.0f} | {r['cnt']:4d}  {bar}")
    print()
    
    # Timezone distribution
    print("  Timezone Distribution (qualified, no domain):")
    rows = conn.execute("""
        SELECT COALESCE(NULLIF(timezone, ''), '(none)') AS tz, COUNT(*) as cnt
        FROM companies
        WHERE qualified = 1 AND (domain IS NULL OR domain = '')
        GROUP BY tz
        ORDER BY cnt DESC
        LIMIT 10
    """).fetchall()
    for r in rows:
        print(f"    {r['tz']:20s} : {r['cnt']}")
    
    conn.close()


def list_companies(args):
    query, params = build_query(args)
    conn = get_conn()
    
    if args.limit:
        query += f" LIMIT {args.limit}"
    
    rows = conn.execute(query, params).fetchall()
    
    if not rows:
        print("No companies match your filters.")
        conn.close()
        return
    
    if args.csv:
        writer = csv.writer(sys.stdout)
        writer.writerow(["ID", "Name", "Domain", "Website", "Headcount", 
                         "Score", "Timezone", "Source", "Contact Count",
                         "Email Count", "Careers URL", "Applied"])
        for r in rows:
            writer.writerow([
                r["id"], r["name"], r["domain"], r["website"], r["headcount"],
                r["remote_score"], r["timezone"], r["source"], r["contact_count"],
                r["email_count"], r["careers_url"], r["applied"]
            ])
    else:
        print(f"\n  Found {len(rows)} companies:\n")
        print(f"  {'ID':>4}  {'Score':>5}  {'Contacts':>8}  {'Emails':>6}  {'Source':16s}  {'Timezone':15s}  {'Domain':25s}  {'Headcount':>5}  {'Applied':>7}  Name")
        print(f"  {'-'*4}  {'-'*5}  {'-'*8}  {'-'*6}  {'-'*16}  {'-'*15}  {'-'*25}  {'-'*5}  {'-'*7}  {'-'*30}")
        for r in rows:
            domain = r["domain"] or "(none)"
            tz = r["timezone"] or ""
            print(f"  {r['id']:>4}  {r['remote_score']:>5.0f}  {r['contact_count']:>8}  {r['email_count']:>6}  {r['source']:16s}  {tz:15s}  {domain:25s}  {r['headcount'] or 0:>5}  {'✅' if r['applied'] else '❌':>7}  {r['name'][:40]}")
    
    conn.close()


def find_careers_urls(args):
    """Find career page URLs for filtered companies."""
    query, params = build_query(args)
    conn = get_conn()
    
    rows = conn.execute(query, params).fetchall()
    
    print(f"\n  🔍 Would search for career URLs on {len(rows)} companies:\n")
    for r in rows:
        domain = r["domain"] or "?"
        website = r["website"] or "?"
        print(f"  [{r['id']:>4}] {r['name'][:35]:35s} score={r['remote_score']:.0f}  domain={domain:20s}  site={website}")
    
    print(f"\n  💡 To actually find URLs, run with --fetch flag (TBD)")
    conn.close()


def main():
    parser = argparse.ArgumentParser(description="Filter companies in the job pipeline DB")
    
    # Filter args
    parser.add_argument("--qualified", action="store_true", default=True, help="Only qualified (default)")
    parser.add_argument("--unqualified", action="store_true", help="Only unqualified/pending")
    parser.add_argument("--no-domain", action="store_true", help="Companies without a domain")
    parser.add_argument("--has-domain", action="store_true", help="Companies with a domain")
    parser.add_argument("--min-score", type=float, help="Minimum remote score")
    parser.add_argument("--max-score", type=float, help="Maximum remote score")
    parser.add_argument("--timezone", type=str, help="Filter by timezone (e.g. US/Eastern)")
    parser.add_argument("--no-timezone", action="store_true", help="Companies without timezone")
    parser.add_argument("--source", type=str, help="Filter by source (himalayas, remoteok, etc.)")
    parser.add_argument("--search", type=str, help="Search by company name")
    parser.add_argument("--has-url", action="store_true", help="Has careers_url")
    parser.add_argument("--no-url", action="store_true", help="No careers_url")
    parser.add_argument("--applied", type=lambda x: x.lower() == 'true', nargs='?', const=True, help="Applied (true/false)")
    
    # Output args
    parser.add_argument("--limit", type=int, default=30, help="Max results (default: 30)")
    parser.add_argument("--order", type=str, help="Order by (default: remote_score DESC)")
    parser.add_argument("--csv", action="store_true", help="Output as CSV")
    parser.add_argument("--stats", action="store_true", help="Show pipeline statistics")
    parser.add_argument("--find-urls", action="store_true", help="Preview companies for career URL discovery")
    
    args = parser.parse_args()
    
    if args.stats:
        show_stats(args)
    elif args.find_urls:
        find_careers_urls(args)
    else:
        list_companies(args)


if __name__ == "__main__":
    main()
