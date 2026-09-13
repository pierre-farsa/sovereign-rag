import json
import requests
import time

from pathlib import Path
from bs4 import BeautifulSoup
from urllib.parse import urljoin


PROJECT_ROOT = Path(__file__).resolve().parents[3]

PDF_DIR = PROJECT_ROOT /"data"/"raw"/"assemblee"
METADATA_FILE = PROJECT_ROOT /"data"/"metadata"/"assemblee.jsonl"

BASE_URL = "https://www.assemblee-nationale.fr"

REPORTS_URL = (
    f"{BASE_URL}/dyn/17/organes/commissions-permanentes/"
    "defense/documents?typeDocument=rapport+d%27information&limit=10"
)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; sovereign-rag/0.1)"
}

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


def fetch_html(url: str) -> str:
    """Fetch an HTML page and return its content"""
    time.sleep(0.5)

    response = SESSION.get(url, timeout=30)
    response.raise_for_status()

    return response.text


def fetch_json(url: str) -> dict:
    """Fetch a JSON document and return the parsed data"""
    response = SESSION.get(url, timeout=30)
    response.raise_for_status()

    return response.json()


def extract_report_links(html: str) -> tuple[list[str], str | None]:
    """Extract report URLs and the next page URL"""
    soup = BeautifulSoup(html, "html.parser")

    report_links = []

    for link in soup.find_all("a", href=True):
        href = link["href"]
        text = link.get_text(" ", strip=True)

        if (
            "rapport-information" in href
            and "Rapport d'information" in text
        ):
            report_url = urljoin(BASE_URL, href)

            if report_url not in report_links:
                report_links.append(report_url)

    # Find the "next page" button
    next_link = soup.select_one(
        ".an-pagination--item.next a.inner"
    )

    next_url = None

    if next_link:
        next_url = urljoin(BASE_URL, next_link["href"])

    return report_links, next_url



def extract_resource_urls(report_html: str) -> dict:
    """Extract the PDF and JSON URLs from a report page"""
    soup = BeautifulSoup(report_html, "html.parser")

    resources = {
        "pdf_url": None,
        "json_url": None,
    }

    # The website exposes several document formats through dedicated blocks
    # We currently need the PDF for the corpus and the JSON for metadata
    for block in soup.select("div.an-bloc"):
        text = block.get_text(" ", strip=True)
        link = block.find("a", href=True)

        if not link:
            continue

        href = urljoin(BASE_URL, link["href"])

        if text == "Version PDF":
            resources["pdf_url"] = href

        elif text == "Notice JSON":
            resources["json_url"] = href

    return resources


def extract_metadata(json_data: dict, source_url: str, pdf_url: str, json_url: str) -> dict:
    """Extract useful metadata from the open data JSON"""
    lifecycle = json_data.get("cycleDeVie", {})
    titles = json_data.get("titres", {})
    notice = json_data.get("notice", {})

    metadata = {
        "id": json_data.get("uid"),
        "institution": "assemblee_nationale",
        "legislature": json_data.get("legislature"),
        "document_type": json_data.get("denominationStructurelle"),
        "report_number": notice.get("numNotice"),
        "title": titles.get("titrePrincipal"),
        "date_depot": lifecycle.get("chrono", {}).get("dateDepot"),
        "date_publication_web": lifecycle.get("chrono", {}).get(
            "datePublicationWeb"
        ),
        "source_url": source_url,
        "pdf_url": pdf_url,
        "json_url": json_url,
    }

    return metadata


def download_pdf(url: str, destination: Path) -> None:
    """Download a PDF file to the given destination"""
    time.sleep(0.5)

    response = SESSION.get(url, timeout=60)
    response.raise_for_status()

    destination.write_bytes(response.content)


def scrape_report(report_url: str) -> dict:
    """Scrape one report and return its metadata"""
    print(f"Processing: {report_url}")

    report_html = fetch_html(report_url)
    resources = extract_resource_urls(report_html)

    pdf_url = resources["pdf_url"]
    json_url = resources["json_url"]

    if not pdf_url:
        raise ValueError(f"PDF URL not found for {report_url}")

    if not json_url:
        raise ValueError(f"JSON URL not found for {report_url}")

    json_data = fetch_json(json_url)

    metadata = extract_metadata(
        json_data=json_data,
        source_url=report_url,
        pdf_url=pdf_url,
        json_url=json_url,
    )

    # Use the website document ID as the local filename
    pdf_path = PDF_DIR / f"{metadata['id']}.pdf"

    if not pdf_path.exists():
        print(f"Downloading PDF: {pdf_path}")
        download_pdf(pdf_url, pdf_path)
    else:
        print(f"PDF already exists: {pdf_path}")

    metadata["local_pdf_path"] = str(
        pdf_path.relative_to(PROJECT_ROOT)
    )

    return metadata


def main() -> None:
    """Scrape 'Assemblée nationale' information reports"""
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    METADATA_FILE.parent.mkdir(parents=True, exist_ok=True)

    all_report_links = []
    current_url = REPORTS_URL

    while current_url:
        print(f"Fetching listing page: {current_url}")

        listing_html = fetch_html(current_url)

        report_links, next_url = extract_report_links(listing_html)

        all_report_links.extend(report_links)

        current_url = next_url

    # Remove duplicates while preserving order
    all_report_links = list(dict.fromkeys(all_report_links))

    print(f"Found {len(all_report_links)} reports\n")

    with METADATA_FILE.open("w", encoding="utf-8") as file:
        for report_url in all_report_links:
            try:
                metadata = scrape_report(report_url)

                json.dump(
                    metadata,
                    file,
                    ensure_ascii=False,
                )
                file.write("\n")

                print(f"Saved: {metadata['id']}\n")

            except requests.RequestException as error:
                print(f"Request failed for {report_url}: {error}")

            except ValueError as error:
                print(f"Extraction failed for {report_url}: {error}")


if __name__ == "__main__":
    main()
