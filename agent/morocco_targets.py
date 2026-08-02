#!/usr/bin/env python3
"""
Moroccan IT company list — multinationals with MA offices + local players.
Domains verified by DNS before use.
"""
import dns.resolver

# (company, domain, category)
MOROCCO_IT_COMPANIES = [
    # ── Multinationals with Moroccan offices (global domains) ──
    ("Accenture Maroc",        "accenture.com",       "multinational"),
    ("Capgemini Morocco",      "capgemini.com",       "multinational"),
    ("IBM Maroc",              "ibm.com",             "multinational"),
    ("Oracle Maroc",           "oracle.com",          "multinational"),
    ("Sopra Steria Maroc",     "soprasteria.com",     "multinational"),
    ("CGI Maroc",              "cgi.com",             "multinational"),
    ("Atos Maroc",             "atos.net",            "multinational"),
    ("Deloitte Maroc",         "deloitte.com",        "multinational"),
    ("Talan Maroc",            "talan.com",           "multinational"),
    ("Altran / Capgemini Eng", "altran.com",          "multinational"),
    ("EY Maroc",               "ey.com",              "multinational"),
    ("PwC Maroc",              "pwc.com",             "multinational"),
    ("KPMG Maroc",             "kpmg.com",            "multinational"),
    ("DXC Technology Maroc",   "dxc.com",             "multinational"),
    ("Wipro Maroc",            "wipro.com",           "multinational"),
    ("Infosys Maroc",          "infosys.com",         "multinational"),
    ("Cognizant Maroc",        "cognizant.com",       "multinational"),
    ("HPE Maroc",              "hpe.com",             "multinational"),
    ("SAP Maroc",              "sap.com",             "multinational"),
    ("Microsoft Maroc",        "microsoft.com",       "multinational"),
    ("Salesforce Maroc",       "salesforce.com",      "multinational"),
    ("Sage Maroc",             "sage.com",            "multinational"),
    ("Sopra Banking",          "soprabanking.com",    "multinational"),
    ("Oberthur Fiduciaire",    "idemia.com",          "multinational"),

    # ── Large Moroccan groups / banks / telecom ──
    ("OCP Group",              "ocpgroup.ma",         "local_large"),
    ("Attijariwafa Bank",      "attijariwafabank.com","local_large"),
    ("Banque Centrale Populaire", "groupebcp.com",    "local_large"),
    ("BMCE Bank",              "bmcebank.com",        "local_large"),
    ("CIH Bank",               "cihbank.com",         "local_large"),
    ("Bank of Africa",         "bankofafrica.ma",     "local_large"),
    ("Maroc Telecom",          "iam.ma",              "local_large"),
    ("Inwi",                   "inwi.ma",             "local_large"),
    ("Orange Maroc",           "orange.ma",           "local_large"),
    ("CDG",                    "cdg.ma",              "local_large"),
    ("Marsa Maroc",            "marsamaroc.co.ma",    "local_large"),
    ("ONCF",                   "oncf.ma",             "local_large"),
    ("Royal Air Maroc",        "royalairmaroc.com",   "local_large"),
    ("Ynnea (ex-Lafarge)",     "ynnea.com",           "local_large"),
    ("Orealis",                "orealiscasablanca.com", "local_large"),

    # ── Moroccan tech companies / startups (from TechBehemoths) ──
    ("Deadline",               "deadline.ma",         "local_tech"),
    ("BerryNoon",              "berrynoon.ma",        "local_tech"),
    ("Iubi",                   "iubi.ma",             "local_tech"),
    ("PM Code Consulting",     "pmcodeconsulting.ma", "local_tech"),
    ("Guide Web",              "guide-web.ma",        "local_tech"),
    ("Morsof",                 "morsof.com",          "local_tech"),
    ("Diavnet",                "diavnet.com",         "local_tech"),
    ("Curly Bracket Dev",      "curlybracketdev.com", "local_tech"),
    ("Junkies Coder",          "junkiescoder.com",    "local_tech"),
    ("Sayt Digital",           "saytdigital.com",     "local_tech"),
    ("Chari",                  "chari.ma",            "startup"),
    ("Kitea",                  "kitea.ma",            "startup"),
    ("Zandit",                 "zandit.ma",           "startup"),
    ("Foxize",                 "foxize.io",           "startup"),
    ("Wetix",                  "wetix.ma",            "startup"),
]


def verify_domains() -> list:
    """Keep only companies whose domain has valid DNS."""
    good = []
    for name, domain, cat in MOROCCO_IT_COMPANIES:
        try:
            dns.resolver.resolve(domain, 'MX', lifetime=4)
            good.append((name, domain, cat))
        except Exception:
            try:
                dns.resolver.resolve(domain, 'A', lifetime=4)
                good.append((name, domain, cat))
            except Exception:
                print(f'  ✗ {name:30s} {domain:25s} — DNS DEAD')
    return good


if __name__ == '__main__':
    print(f"Total list: {len(MOROCCO_IT_COMPANIES)} companies")
    print("Verifying domains...\n")
    good = verify_domains()
    print(f"\n✅ {len(good)}/{len(MOROCCO_IT_COMPANIES)} have live domains")
    from collections import Counter
    cats = Counter(c for _, _, c in good)
    print(f"   Categories: {dict(cats)}")
