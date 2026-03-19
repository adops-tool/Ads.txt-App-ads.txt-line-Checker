"""Streamlit application for validating ads.txt and app-ads.txt entries."""

import streamlit as st
import pandas as pd
import requests
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import random
import os
from urllib.parse import urlparse

icon_path = "icons/icon.png"
page_icon = icon_path if os.path.exists(icon_path) else None

st.set_page_config(
    page_title="Ads.txt / App-ads.txt line Checker", 
    layout="wide", 
    page_icon=page_icon
)

st.markdown("""
    <style>
    .stApp {
        background-color: #1e1e1e;
        color: #e0e0e0;
    }
    
    h1, h2, h3, .stMarkdown h1, .stMarkdown h2, .stMarkdown h3 {
        color: #ffffff !important;
    }
    
    h3 {
        padding-top: 10px !important;
        padding-bottom: 5px !important;
    }
    
    .stTextArea textarea {
        background-color: #2d2d2d !important;
        color: #ffffff !important;
        border: 1px solid #444 !important;
    }
    .stTextArea textarea:focus {
        border-color: #21aeb3 !important;
        box-shadow: 0 0 0 1px #21aeb3 !important;
    }
    
    div.stButton > button {
        background-color: #21aeb3 !important;
        color: white !important;
        border: none !important;
        font-weight: bold !important;
        transition: 0.3s;
    }
    div.stButton > button:hover {
        background-color: #1a8a8e !important;
        border: 1px solid #ffffff !important;
    }
    
    div[data-baseweb="notification"] {
        background-color: #2d2d2d !important;
        border: 1px solid #21aeb3 !important;
        color: #ffffff !important;
    }
    div[data-baseweb="notification"] svg {
        fill: #21aeb3 !important;
    }
    
    div[data-testid="stDataFrame"] {
        background-color: #2d2d2d;
    }
    
    .stRadio label, .stMultiSelect label {
        color: #e0e0e0 !important;
    }
    div[role="radiogroup"] div[aria-checked="true"] div:first-child {
        background-color: #21aeb3 !important;
        border-color: #21aeb3 !important;
    }
    div[role="radiogroup"] > div {
        color: #e0e0e0 !important;
    }

    .stProgress > div > div > div > div {
        background-color: #21aeb3 !important;
    }

    .stRadio {
        margin-top: 0px !important;
        margin-bottom: 0px !important;
    }
    
    div[data-testid="column"] > div > div {
        gap: 0.5rem !important;
    }
    
    .compact-hr {
        margin: 10px 0 !important;
        border: 0;
        border-top: 1px solid #444;
    }
    
    [data-testid="stImage"] {
        margin-top: 15px;
    }
    
    .stMultiSelect span {
        color: #e0e0e0 !important;
    }
    </style>
""", unsafe_allow_html=True)

col_head1, col_head2 = st.columns([0.6, 12])
with col_head1:
    if os.path.exists(icon_path):
        st.image(icon_path, width=65)
with col_head2:
    st.title("Ads.txt / App-ads.txt line Checker")

LIVE_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

if 'results_df' not in st.session_state:
    st.session_state.results_df = None
if 'all_results_raw' not in st.session_state:
    st.session_state.all_results_raw = None
if 'processed_refs' not in st.session_state:
    st.session_state.processed_refs = []
if 'target_order' not in st.session_state:
    st.session_state.target_order = []

def get_session():
    """Create an HTTP session configured for ads.txt retrieval requests.

    Returns:
        requests.Session: A session with headers tailored for fetching
            plain-text ads.txt and app-ads.txt files.

    Examples:
        >>> session = get_session()
        >>> session.headers["User-Agent"] == LIVE_UA
        True
    """
    s = requests.Session()
    s.headers.update({
        'User-Agent': LIVE_UA,
        'Accept': 'text/plain,text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Cache-Control': 'no-cache',
    })
    return s

def get_clean_domain(url_input):
    """Normalize a user-provided URL or hostname into a domain string.

    Args:
        url_input (str): A hostname or URL entered by the user. If the value
            omits a scheme, the function prepends ``http://`` before parsing.

    Returns:
        str: The normalized network location or best-effort hostname extracted
            from the input string.

    Examples:
        >>> get_clean_domain("example.com")
        'example.com'
        >>> get_clean_domain("https://sub.example.com/path")
        'sub.example.com'
    """
    if not url_input.startswith(("http://", "https://")):
        url_input = "http://" + url_input
    try:
        parsed = urlparse(url_input)
        return parsed.netloc if parsed.netloc else parsed.path.split('/')[0]
    except:
        return url_input.strip()

def fetch_file_content(domain, filename):
    """Fetch an ads.txt-style file from a target domain.

    Args:
        domain (str): The raw target website or hostname to query.
        filename (str): The file name to retrieve, typically ``ads.txt`` or
            ``app-ads.txt``.

    Returns:
        tuple[str | None, str, bool]: A tuple containing the fetched file
        content when available, a human-readable status message, and a boolean
        that indicates whether the request should be treated as an error.

    Examples:
        >>> content, status, is_error = fetch_file_content("example.com", "ads.txt")
        >>> isinstance(status, str)
        True

    Raises:
        requests.RequestException: Propagated only when an unexpected requests
            error escapes the internal retry handling logic.
    """
    session = get_session()
    domain_clean = get_clean_domain(domain)
    
    urls = [
        f"https://{domain_clean}/{filename}",
        f"http://{domain_clean}/{filename}"
    ]
    
    last_error = "Unknown error"

    for url in urls:
        try:
            time.sleep(random.uniform(0.5, 1.5))
            response = session.get(url, timeout=15, allow_redirects=True)
            
            if response.status_code == 200:
                text = response.text
                if "<!doctype html" in text.lower() or text.lstrip().lower().startswith("<html"):
                    return None, "Error: HTML Page instead of txt", True
                return text, "OK", False
                
            last_error = f"HTTP {response.status_code}"
        except requests.exceptions.SSLError:
            try:
                time.sleep(1)
                response = session.get(url, timeout=15, allow_redirects=True, verify=False)
                if response.status_code == 200:
                    text = response.text
                    if "<!doctype html" in text.lower() or text.lstrip().lower().startswith("<html"):
                        return None, "Error: HTML Page instead of txt", True
                    return text, "OK (SSL Warning)", False
            except Exception as e:
                last_error = str(e)
        except Exception as e:
            last_error = str(e)
            
    return None, f"Not accessible: {last_error}", True

def parse_ads_file(content):
    """Parse ads.txt or app-ads.txt content into structured records.

    Args:
        content (str | None): Raw file content returned from a remote target.
            Empty values produce an empty result set.

    Returns:
        list[dict[str, str | None]]: A list of parsed records containing the
        advertising system domain, seller account ID, and optional account
        type for each valid non-comment line.

    Examples:
        >>> parse_ads_file("exchange.com, pub-123, DIRECT\n")
        [{'domain': 'exchange.com', 'id': 'pub-123', 'type': 'DIRECT'}]
    """
    parsed_lines = []
    if not content:
        return parsed_lines
    for line in content.splitlines():
        clean_line = line.split('#')[0].strip()
        if not clean_line:
            continue
        parts = [p.strip() for p in clean_line.split(',')]
        if len(parts) >= 2:
            parsed_lines.append({
                'domain': parts[0].lower(),
                'id': parts[1].lower(),
                'type': parts[2].upper() if len(parts) > 2 else None,
            })
    return parsed_lines

def parse_reference_line(line):
    """Parse a user-supplied reference rule into a comparable record.

    Args:
        line (str): A comma-separated reference line containing at least a
            domain and seller ID, with an optional relationship type.

    Returns:
        dict[str, str | None] | None: A normalized reference dictionary when
        the line includes at least two comma-separated values; otherwise,
        ``None``.

    Examples:
        >>> parse_reference_line("onetag.com, 5d0d72448d8bfb0, DIRECT")['type']
        'DIRECT'
        >>> parse_reference_line("invalid") is None
        True
    """
    parts = [p.strip() for p in line.split(',')]
    if len(parts) < 2:
        return None
    return {
        'domain': parts[0].lower(),
        'id': parts[1].lower(),
        'type': parts[2].upper() if len(parts) > 2 else None,
        'original': line
    }

def validate_domain(target_domain, filename, references):
    """Validate a target domain against one or more reference entries.

    Args:
        target_domain (str): The website whose ``ads.txt`` or ``app-ads.txt``
            file should be checked.
        filename (str): The file name to validate, such as ``ads.txt`` or
            ``app-ads.txt``.
        references (list[dict[str, str | None]]): Parsed reference entries to
            match against the remote file content. Each dictionary is expected
            to contain ``domain``, ``id``, ``type``, and ``original`` keys.

    Returns:
        list[dict[str, str]]: A result row for every reference entry, including
        the target URL, checked file name, match outcome, explanatory details,
        and the original reference string.

    Examples:
        >>> refs = [{
        ...     'domain': 'exchange.com',
        ...     'id': 'pub-123',
        ...     'type': 'DIRECT',
        ...     'original': 'exchange.com, pub-123, DIRECT',
        ... }]
        >>> isinstance(validate_domain('example.com', 'ads.txt', refs), list)
        True
    """
    content, status_msg, is_error = fetch_file_content(target_domain, filename)
    results = []
    
    if is_error:
        for ref in references:
            results.append({
                "URL": target_domain,
                "File": filename,
                "Result": "Error",
                "Details": status_msg,
                "Reference": ref['original']
            })
        return results

    file_records = parse_ads_file(content)
    
    for ref in references:
        ref_domain = ref['domain']
        ref_id = ref['id']
        ref_type = ref['type']
        
        match_status = "Not found"
        details = "No matching Domain+ID pair"
        
        for record in file_records:
            if record['domain'] == ref_domain and record['id'] == ref_id:
                if not ref_type:
                    match_status = "Valid"
                    details = "Matched by Domain + ID"
                    break
                if record['type'] == ref_type:
                    match_status = "Valid"
                    details = "Full match"
                    break
                else:
                    match_status = "Partially matched"
                    details = f"Type mismatch: found {record['type']}, expected {ref_type}"
        
        results.append({
            "URL": target_domain,
            "File": filename,
            "Result": match_status,
            "Details": details,
            "Reference": ref['original']
        })
        
    return results

st.subheader("Settings")
set_col1, set_col2 = st.columns(2)

with set_col1:
    file_type = st.radio(
        "File Type",
        ("app-ads.txt", "ads.txt")
    )
    
    st.markdown('<div class="compact-hr"></div>', unsafe_allow_html=True)
    
    view_mode = st.radio(
        "Output View",
        ("Show All Results", "Errors / Warnings Only"),
        index=1
    )

with set_col2:
    layout_mode = st.radio(
        "Result Layout",
        ("Standard (Vertical)", "Horizontal (Aggregated)"),
        index=0
    )
    
    error_filters = []
    if view_mode == "Errors / Warnings Only":
        st.markdown('<div class="compact-hr"></div>', unsafe_allow_html=True)
        
        err_options = [
            "Type mismatch: found RESELLER, expected DIRECT",
            "Not Found (No Domain+ID)",
            "Other Partial Matches",
            "Connection / System Errors"
        ]
        
        error_filters = st.multiselect(
            "Filter specific errors:",
            options=err_options,
            default=[
                "Not Found (No Domain+ID)",
                "Other Partial Matches",
                "Connection / System Errors"
            ]
        )

st.markdown('<div class="compact-hr" style="margin: 15px 0 !important;"></div>', unsafe_allow_html=True)

st.subheader("Input Data")
col1, col2 = st.columns(2)

with col1:
    st.markdown("**Target Websites**")
    target_input = st.text_area(
        "Label hidden", 
        height=300,
        placeholder="example.com\nmygame.site",
        label_visibility="collapsed"
    )

with col2:
    st.markdown("**Reference Lines (Rules)**")
    ref_input = st.text_area(
        "Label hidden", 
        height=300, 
        placeholder="onetag.com, 5d0d72448d8bfb0, DIRECT",
        label_visibility="collapsed"
    )

start_btn = st.button("Start Validation")

if start_btn:
    if not target_input or not ref_input:
        st.warning("Please fill both windows.")
    else:
        targets = [t.strip() for t in target_input.splitlines() if t.strip()]
        st.session_state.target_order = targets
        
        references = []
        raw_refs = [r.strip() for r in ref_input.splitlines() if r.strip()]
        for r in raw_refs:
            parsed = parse_reference_line(r)
            if parsed:
                references.append(parsed)
        
        st.session_state.processed_refs = references

        if not references:
            st.warning("No valid reference lines found.")
            st.stop()

        progress_bar = st.progress(0)
        status_text = st.empty()
        all_results = []
        MAX_WORKERS = 5 
        
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            future_to_url = {
                executor.submit(validate_domain, url, file_type, references): url 
                for url in targets
            }
            
            for i, future in enumerate(as_completed(future_to_url)):
                url = future_to_url[future]
                try:
                    data = future.result()
                    all_results.extend(data)
                except Exception as e:
                    all_results.append({
                        "URL": url, "File": file_type, 
                        "Result": "System Error", "Details": str(e), 
                        "Reference": "-"
                    })
                
                progress = (i + 1) / len(targets)
                progress_bar.progress(progress)
                status_text.text(f"Checking: {url} ...")

        progress_bar.empty()
        status_text.empty()
        
        st.session_state.all_results_raw = all_results

if st.session_state.all_results_raw is not None:
    st.markdown('<div class="compact-hr"></div>', unsafe_allow_html=True)
    st.subheader("Results")
    
    raw_data = st.session_state.all_results_raw
    final_df = pd.DataFrame(raw_data)
    
    target_order_map = {url: i for i, url in enumerate(st.session_state.target_order)}
    final_df['SortIndex'] = final_df['URL'].map(target_order_map)
    final_df = final_df.sort_values('SortIndex').drop(columns=['SortIndex'])
    
    def classify_error(row):
        """Map a result row to a UI filter category.

        Args:
            row (pd.Series): A DataFrame row containing at least ``Result`` and
                ``Details`` fields.

        Returns:
            str: The display category used by the error filter multiselect.

        Examples:
            >>> classify_error(pd.Series({'Result': 'Valid', 'Details': 'Full match'}))
            'Valid'
        """
        r = row['Result']
        d = row['Details']
        
        if r == "Valid":
            return "Valid"
        if "found RESELLER, expected DIRECT" in d:
            return "Type mismatch: found RESELLER, expected DIRECT"
        if "No matching Domain+ID pair" in d:
            return "Not Found (No Domain+ID)"
        if r == "Partially matched":
            return "Other Partial Matches"
        if r == "Error" or "System Error" in r or "HTTP" in d:
            return "Connection / System Errors"
        return "Other"

    if view_mode == "Errors / Warnings Only":
        final_df['ErrorCategory'] = final_df.apply(classify_error, axis=1)
        final_df = final_df[final_df['ErrorCategory'].isin(error_filters)]
        final_df = final_df.drop(columns=['ErrorCategory'])

    if final_df.empty and view_mode == "Errors / Warnings Only" and raw_data:
        st.success("🎉 Great job! No errors found based on current filters.")
    elif final_df.empty and not raw_data:
        st.info("No results to display.")
    else:
        def color_status(val):
            """Return a CSS style string for a result status value.

            Args:
                val (str): The result label displayed in the output table.

            Returns:
                str: A CSS declaration string used by pandas styling.

            Examples:
                >>> color_status('Valid')
                'background-color: #21aeb3; color: white'
            """
            if val == "Valid":
                return 'background-color: #21aeb3; color: white' 
            elif val == "Partially matched":
                return 'background-color: #000000; color: #21aeb3; font-weight: bold'
            elif val == "Not found":
                return 'background-color: #383838; color: #aaaaaa'
            elif val == "Error":
                return 'background-color: #2d2d2d; color: #888888'
            return ''

        display_df = pd.DataFrame()

        if layout_mode == "Horizontal (Aggregated)":
            refs = st.session_state.processed_refs
            unique_targets = sorted(list(set(final_df['URL'])), key=lambda x: target_order_map.get(x, 9999))
            
            rows = []
            for url in unique_targets:
                url_rows = final_df[final_df['URL'] == url]
                
                row_data = {
                    "URL": url,
                    "File": file_type
                }
                
                has_visible_data = False
                
                for i, ref in enumerate(refs):
                    idx = i + 1
                    col_res = f"Result {idx}"
                    col_det = f"Details {idx}"
                    col_ref = f"Ref {idx}"
                    
                    match = url_rows[url_rows['Reference'] == ref['original']]
                    
                    if not match.empty:
                        has_visible_data = True
                        row_data[col_res] = match.iloc[0]['Result']
                        row_data[col_det] = match.iloc[0]['Details']
                        row_data[col_ref] = match.iloc[0]['Reference']
                    else:
                        row_data[col_res] = "Filtered"
                        row_data[col_det] = "-"
                        row_data[col_ref] = ref['original']
                
                if has_visible_data:
                    rows.append(row_data)
            
            display_df = pd.DataFrame(rows)
            
            result_cols = [c for c in display_df.columns if c.startswith("Result")]
            styled_df = display_df.style.map(color_status, subset=result_cols)
            
        else:
            display_df = final_df[["URL", "File", "Result", "Details", "Reference"]]
            styled_df = display_df.style.map(color_status, subset=['Result'])

        st.dataframe(
            styled_df,
            use_container_width=True,
            height=600
        )
        csv = display_df.to_csv(index=False).encode('utf-8-sig')

        st.download_button(
            label=f"Download CSV ({view_mode})",
            data=csv,
            file_name="report.csv",
            mime="text/csv",
        )
