import re
import requests
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("Xiaozhi_Vietnam_KHKT")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

def clean_html(raw_html: str) -> str:
    """Loại bỏ thẻ HTML và khoảng trắng thừa trong tóm tắt RSS."""
    if not raw_html:
        return ""
    soup = BeautifulSoup(raw_html, "html.parser")
    text = soup.get_text(separator=" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()

def fetch_rss_news(rss_url: str, source_name: str, limit: int = 5) -> str:
    """Hàm đọc và trích xuất tin tức từ nguồn RSS."""
    try:
        resp = requests.get(rss_url, headers=HEADERS, timeout=10)
        resp.encoding = "utf-8"
        root = ET.fromstring(resp.text)
        items = root.findall(".//item")[:limit]

        if not items:
            return f"Hiện chưa lấy được danh sách tin từ báo {source_name}."

        results = [f"Các tin mới nhất từ báo {source_name}:"]
        for idx, item in enumerate(items, 1):
            title = item.findtext("title", "").strip()
            desc = clean_html(item.findtext("description", ""))
            results.append(f"{idx}. {title}. Tóm tắt: {desc}")

        return "\n".join(results)
    except Exception as e:
        return f"Lỗi khi kết nối tới báo {source_name}: {str(e)}"


# ==========================================
# TOOL 1: ĐỌC BÁO VNEXPRESS
# ==========================================
@mcp.tool()
def doc_bao_vnexpress(chu_de: str = "tin_moi", so_luong: int = 5) -> str:
    """
    Đọc tin tức mới nhất từ báo VnExpress theo chủ đề.
    Khi người dùng yêu cầu đọc báo VnExpress hoặc hỏi tin tức thời sự, thể thao, công nghệ, giáo dục trên VnExpress thì gọi công cụ này.
    Tham số chu_de nhận một trong các giá trị: 'tin_moi', 'thoi_su', 'the_gioi', 'kinh_doanh', 'giai_tri', 'the_thao', 'phap_luat', 'giao_duc', 'suc_khoe', 'khoa_hoc', 'so_hoa'.
    """
    rss_map = {
        "tin_moi": "https://vnexpress.net/rss/tin-moi-nhat.rss",
        "thoi_su": "https://vnexpress.net/rss/thoi-su.rss",
        "the_gioi": "https://vnexpress.net/rss/the-gioi.rss",
        "kinh_doanh": "https://vnexpress.net/rss/kinh-doanh.rss",
        "giai_tri": "https://vnexpress.net/rss/giai-tri.rss",
        "the_thao": "https://vnexpress.net/rss/the-thao.rss",
        "phap_luat": "https://vnexpress.net/rss/phap-luat.rss",
        "giao_duc": "https://vnexpress.net/rss/giao-duc.rss",
        "suc_khoe": "https://vnexpress.net/rss/suc-khoe.rss",
        "khoa_hoc": "https://vnexpress.net/rss/khoa-hoc.rss",
        "so_hoa": "https://vnexpress.net/rss/so-hoa.rss",
    }
    url = rss_map.get(chu_de.lower().strip(), rss_map["tin_moi"])
    so_luong = max(1, min(so_luong, 8))
    return fetch_rss_news(url, "VnExpress", so_luong)


# ==========================================
# TOOL 2: ĐỌC BÁO TUỔI TRẺ
# ==========================================
@mcp.tool()
def doc_bao_tuoi_tre(chu_de: str = "tin_moi", so_luong: int = 5) -> str:
    """
    Đọc tin tức mới nhất từ báo Tuổi Trẻ Online (tuoitre.vn) theo chủ đề.
    Khi người dùng yêu cầu đọc báo Tuổi Trẻ hoặc hỏi tin tức trên báo Tuổi Trẻ thì gọi công cụ này.
    Tham số chu_de nhận một trong các giá trị: 'tin_moi', 'thoi_su', 'the_gioi', 'phap_luat', 'kinh_doanh', 'cong_nghe', 'nhip_song_tre', 'van_hoa', 'giai_tri', 'the_thao', 'giao_duc', 'suc_khoe'.
    """
    rss_map = {
        "tin_moi": "https://tuoitre.vn/rss/tin-moi-nhat.rss",
        "thoi_su": "https://tuoitre.vn/rss/thoi-su.rss",
        "the_gioi": "https://tuoitre.vn/rss/the-gioi.rss",
        "phap_luat": "https://tuoitre.vn/rss/phap-luat.rss",
        "kinh_doanh": "https://tuoitre.vn/rss/kinh-doanh.rss",
        "cong_nghe": "https://tuoitre.vn/rss/nhip-song-so.rss",
        "nhip_song_tre": "https://tuoitre.vn/rss/nhip-song-tre.rss",
        "van_hoa": "https://tuoitre.vn/rss/van-hoa.rss",
        "giai_tri": "https://tuoitre.vn/rss/giai-tri.rss",
        "the_thao": "https://tuoitre.vn/rss/the-thao.rss",
        "giao_duc": "https://tuoitre.vn/rss/giao-duc.rss",
        "suc_khoe": "https://tuoitre.vn/rss/suc-khoe.rss",
    }
    url = rss_map.get(chu_de.lower().strip(), rss_map["tin_moi"])
    so_luong = max(1, min(so_luong, 8))
    return fetch_rss_news(url, "Tuổi Trẻ", so_luong)


# ==========================================
# TOOL 3: TRA CỨU GIÁ VÀNG HÔM NAY
# ==========================================
@mcp.tool()
def tra_cuu_gia_vang() -> str:
    """
    Tra cứu giá vàng hôm nay tại Việt Nam (gồm vàng miếng SJC, vàng nhẫn trơn 9999 PNJ/DOJI/Bảo Tín Minh Châu).
    Gọi công cụ này bất cứ khi nào người dùng hỏi giá vàng hôm nay bao nhiêu, giá vàng SJC, giá vàng 9999.
    """
    # Nguồn 1: Quét bảng giá vàng trực tuyến từ Webgia.com (Cập nhật liên tục theo SJC/DOJI/PNJ)
    try:
        resp = requests.get("https://webgia.com/gia-vang/sjc/", headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "lxml")
            table = soup.find("table", class_="table")
            if table:
                rows = table.find_all("tr")
                lines = ["Bảng giá vàng SJC cập nhật mới nhất hôm nay:"]
                count = 0
                for row in rows:
                    cols = [c.get_text(" ", strip=True) for c in row.find_all(["td", "th"])]
                    if len(cols) >= 3 and any(char.isdigit() for char in cols[-1]):
                        lines.append(" - ".join(cols[:4]))
                        count += 1
                        if count >= 6:
                            break
                if count > 0:
                    return "\n".join(lines)
    except Exception:
        pass

    # Nguồn 2 (Dự phòng): Quét bảng giá vàng tổng hợp từ 24h.com.vn
    try:
        resp = requests.get("https://www.24h.com.vn/gia-vang-hom-nay-c425.html", headers=HEADERS, timeout=10)
        if resp.status_code == 200:
            soup = BeautifulSoup(resp.text, "lxml")
            rows = soup.select("table.gia-vang-search-data-table tr")
            lines = ["Bảng giá vàng trong nước cập nhật mới nhất:"]
            for row in rows[:6]:
                cols = [c.get_text(" ", strip=True) for c in row.find_all("td")]
                if len(cols) >= 3:
                    lines.append(f"{cols[0]}: Mua vào {cols[1]} - Bán ra {cols[2]}")
            if len(lines) > 1:
                return "\n".join(lines)
    except Exception:
        pass

    # Nguồn 3 (Dự phòng cuối): Lấy tin bản tin giá vàng mới nhất từ Google News RSS Việt Nam
    return fetch_rss_news(
        "https://news.google.com/rss/search?q=gi%C3%A1+v%C3%A0ng+SJC+9999+h%C3%B4m+nay&hl=vi&gl=VN&ceid=VN:vi",
        "Bản tin Giá Vàng Việt Nam",
        limit=3
    )


if __name__ == "__main__":
    mcp.run(transport="stdio")