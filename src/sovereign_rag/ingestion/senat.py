import json
import time
import re
import requests

from pathlib import Path
from bs4 import BeautifulSoup
from urllib.parse import urljoin


PROJECT_ROOT = Path(__file__).resolve().parents[3]

PDF_DIR = PROJECT_ROOT /"data"/"raw"/"test"/"senat"
METADATA_FILE = PROJECT_ROOT /"data"/"metadata"/"test"/"senat.jsonl"

BASE_URL = "https://www.senat.fr"

REPORTS_URL = (
    f"{BASE_URL}/themes/rapports-defense.html"
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

    response.encoding = "utf-8"

    return response.text


def extract_report_links(html: str) -> list[str]:
    """Extract report URLs"""
    soup = BeautifulSoup(html, "html.parser")

    report_links = []

    for link in soup.find_all("a", href=True):
        href = link["href"]

        if "/notice-rapport/" in href:
            report_url = urljoin(BASE_URL, href)

            if report_url not in report_links:
                report_links.append(report_url)

    return report_links


def extract_resource_urls(notice_html: str, notice_url: str) -> dict:
    """Extract the report URL and PDF URL"""
    soup = BeautifulSoup(notice_html, "html.parser")

    resources = {
        "report_url": None,
        "pdf_url": None,
    }

    # PDF is available at "Consulter le rapport"
    for link in soup.find_all("a", href=True):
        text = link.get_text(" ", strip=True)

        if text == "Consulter le rapport":
            resources["report_url"] = urljoin(
                notice_url,
                link["href"],
            )
            break

    if not resources["report_url"]:
        return resources

    # Some reports point directly to the PDF
    if resources["report_url"].lower().endswith(".pdf"):
        resources["pdf_url"] = resources["report_url"]
        return resources

    report_html = fetch_html(resources["report_url"])
    report_soup = BeautifulSoup(report_html, "html.parser")

    for link in report_soup.find_all("a", href=True):
        text = link.get_text(" ", strip=True)

        if text == "PDF":
            resources["pdf_url"] = urljoin(
                resources["report_url"],
                link["href"],
            )
            break

    return resources


def extract_metadata(notice_html: str, notice_url: str, report_url: str, pdf_url: str) -> dict:
    """Extract metadata from the report notice"""
    soup = BeautifulSoup(notice_html, "html.parser")

    # Title
    title_tag = soup.find("h1")

    if not title_tag:
        raise ValueError(
            f"Title not found for {notice_url}"
        )

    title = title_tag.get_text(" ", strip=True)

    # Report number / session / date
    report_info = None

    for element in soup.find_all(["p", "div", "span"]):
        text = element.get_text(" ", strip=True)

        if (
            text.startswith("Rapport")
            and "n°" in text
            and "déposé le" in text
        ):
            report_info = text
            break

    if not report_info:
        raise ValueError(
            f"Report information not found for {notice_url}"
        )

    match = re.search(
        r"Rapport(?: d'information)?\s+n°\s*"
        r"(\d+)"
        r"\s*\(([^)]+)\)"
        r"(?:,\s*tome\s+[^,]+)?"
        r",\s*déposé le\s*(.+)$",
        report_info,
    )

    if not match:
        raise ValueError(
            f"Could not parse report information "
            f"for {notice_url}"
        )

    report_number = match.group(1)
    session = match.group(2)
    date_depot = match.group(3)

    # Document type
    nature_label = soup.find(
        lambda tag:
        tag.name in ("h3", "dt")
        and tag.get_text(" ", strip=True) == "Nature"
    )

    if not nature_label:
        raise ValueError(
            f"Nature not found for {notice_url}"
        )

    document_type = None

    for sibling in nature_label.next_siblings:
        if not hasattr(sibling, "get_text"):
            continue

        text = sibling.get_text(" ", strip=True)

        if text:
            document_type = text
            break

    if not document_type:
        raise ValueError(
            f"Nature value not found for {notice_url}"
        )

    metadata = {
        "id": Path(notice_url).stem.replace("-notice", ""),
        "institution": "senat",
        "document_type": document_type,
        "report_number": report_number,
        "session": session,
        "title": title,
        "date_depot": date_depot,
        "source_url": notice_url,
        "report_url": report_url,
        "pdf_url": pdf_url,
    }

    return metadata


def download_pdf(url: str, destination: Path) -> None:
    """Download a PDF file to the given destination"""
    time.sleep(0.5)

    response = SESSION.get(url, timeout=60)
    response.raise_for_status()

    destination.write_bytes(response.content)


def scrape_report(notice_url: str) -> dict | None:
    """Scrape one report and download its PDF"""
    print(f"Processing: {notice_url}")

    notice_html = fetch_html(notice_url)

    resources = extract_resource_urls(
        notice_html,
        notice_url,
    )

    report_url = resources["report_url"]
    pdf_url = resources["pdf_url"]

    if not report_url:
        print("Report URL not found\n")
        return None

    if not pdf_url:
        print("PDF URL not found\n")
        return None

    metadata = extract_metadata(
        notice_html=notice_html,
        notice_url=notice_url,
        report_url=report_url,
        pdf_url=pdf_url,
    )

    pdf_path = PDF_DIR / f"{metadata['id']}.pdf"

    if not pdf_path.exists():
        print(f"Downloading PDF: {pdf_path}")
        download_pdf(pdf_url, pdf_path)
    else:
        print(f"PDF already exists: {pdf_path}")

    return metadata


def main() -> None:
    """Scrape 'Sénat' information reports"""
    PDF_DIR.mkdir(parents=True, exist_ok=True)
    METADATA_FILE.parent.mkdir(parents=True, exist_ok=True)

    print(f"Fetching listing page: {REPORTS_URL}")

    listing_html = fetch_html(REPORTS_URL)
    report_links = extract_report_links(listing_html)

    # Remove duplicates while preserving order
    report_links = list(dict.fromkeys(report_links))

    print(f"Found {len(report_links)} reports\n")

    with METADATA_FILE.open("w", encoding="utf-8") as file:
        for report_url in report_links:
            try:
                metadata = scrape_report(report_url)

                if metadata is None:
                    continue

                json.dump(
                    metadata,
                    file,
                    ensure_ascii=False,
                )
                file.write("\n")

                print(f"Saved: {metadata['id']}\n")

            except requests.RequestException as error:
                print(
                    f"Request failed for "
                    f"{report_url}: {error}"
                )

            except ValueError as error:
                print(
                    f"Extraction failed for "
                    f"{report_url}: {error}"
                )


if __name__ == "__main__":
    main()
