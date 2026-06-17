"""
Work at a Startup (Y Combinator) scraper.
Scrapes job listings from workatastartup.com - YC's official job board.
Data is server-side rendered in the HTML (no JS needed).
"""
import requests
import re
import json
import html as html_module
import time
import logging

logger = logging.getLogger(__name__)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
}

BASE_URL = 'https://www.workatastartup.com'


def fetch_jobs():
    """Fetch all jobs from the /jobs page."""
    urls = [
        f'{BASE_URL}/jobs',
        f'{BASE_URL}/companies',
    ]
    
    all_jobs = []
    seen_ids = set()
    
    for url in urls:
        try:
            resp = requests.get(url, headers=HEADERS, timeout=15)
            if resp.status_code != 200:
                logger.warning('Failed to fetch %s: %d', url, resp.status_code)
                continue
            
            # Extract jobs JSON from HTML (HTML-encoded)
            match = re.search(r'&quot;jobs&quot;:(\[.*?\]),&quot;signupUrl&quot;', resp.text, re.DOTALL)
            if not match:
                logger.warning('No jobs data found in %s', url)
                continue
            
            raw = html_module.unescape(match.group(1))
            jobs = json.loads(raw)
            
            for job in jobs:
                if job['id'] not in seen_ids:
                    seen_ids.add(job['id'])
                    all_jobs.append(job)
                    
        except Exception as e:
            logger.error('Error scraping %s: %s', url, e)
    
    logger.info('Scraped %d unique jobs from Y Combinator', len(all_jobs))
    return all_jobs


def extract_companies(jobs):
    """Extract unique companies from job listings."""
    companies = {}
    for j in jobs:
        name = j['companyName']
        if name not in companies:
            companies[name] = {
                'name': name,
                'slug': j['companySlug'],
                'batch': j['companyBatch'],
                'oneliner': j['companyOneLiner'],
                'logo_url': j['companyLogoUrl'],
                'domain': None,
                'stack': None,
                'description': j['companyOneLiner'],
                'headcount': None,
                'source': 'workatastartup',
                'url': f'{BASE_URL}/companies/{j["companySlug"]}',
            }
    return list(companies.values())


def scrape_company_page(slug):
    """Scrape individual company page for more details."""
    url = f'{BASE_URL}/companies/{slug}'
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        if resp.status_code != 200:
            return {}
        
        text = resp.text
        decoded = html_module.unescape(text)
        data = {}
        
        # Extract company description from meta tags
        desc_match = re.search(r'<meta[^>]*name="description"[^>]*content="([^"]+)"', text)
        if desc_match:
            data['description'] = desc_match.group(1)
        
        # Look for jobs at this company
        job_match = re.search(r'&quot;jobs&quot;:(\[.*?\]),&quot;', text, re.DOTALL)
        if job_match:
            raw = html_module.unescape(job_match.group(1))
            data['jobs'] = json.loads(raw)
        
        return data
    except Exception as e:
        logger.error('Error scraping company %s: %s', slug, e)
        return {}


def run(conn, cfg=None):
    """Main entry point: scrape jobs and insert into database."""
    jobs = fetch_jobs()
    companies = extract_companies(jobs)
    
    inserted = 0
    for company in companies:
        try:
            existing = conn.execute(
                'SELECT id FROM companies WHERE name = ? AND source = ?',
                (company['name'], 'workatastartup')
            ).fetchone()
            
            if existing:
                continue
            
            conn.execute(
                '''INSERT INTO companies 
                   (name, domain, description, stack, headcount, source, website)
                   VALUES (?, ?, ?, ?, ?, ?, ?)''',
                (
                    company['name'],
                    company['domain'],
                    company['description'],
                    company['stack'],
                    company['headcount'],
                    company['source'],
                    company['url'],
                )
            )
            inserted += 1
        except Exception as e:
            logger.error('Error inserting company %s: %s', company['name'], e)
    
    conn.commit()
    logger.info('Inserted %d new companies from Y Combinator', inserted)
    return {'scraped': len(companies), 'inserted': inserted}


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    jobs = fetch_jobs()
    companies = extract_companies(jobs)
    print(f'Found {len(jobs)} jobs from {len(companies)} companies')
    for c in companies[:5]:
        print(f'  {c["name"]} - {c["oneliner"][:60]}')
