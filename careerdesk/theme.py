"""Original, offline visual theme for the local CareerDesk interface."""

from __future__ import annotations

import html

import streamlit as st

from careerdesk.game_art import page_scene_html


CSS = r"""
<style>
:root {
  --cd-ink: #294534;
  --cd-muted: #647466;
  --cd-forest: #31533d;
  --cd-leaf: #618c4f;
  --cd-cream: #f5edda;
  --cd-paper: #fff9ea;
  --cd-paper-dark: #f1e0bb;
  --cd-wood: #91683c;
  --cd-wood-dark: #684826;
  --cd-line: #c9ae79;
}
html, body, [data-testid="stApp"] {
  color: var(--cd-ink);
  font-family: "Trebuchet MS", "Microsoft YaHei", "PingFang SC", sans-serif;
}
[data-testid="stApp"] {
  background-color: var(--cd-cream);
  background-image: linear-gradient(90deg, rgba(145, 104, 60, .045) 1px, transparent 1px),
                    linear-gradient(rgba(145, 104, 60, .045) 1px, transparent 1px);
  background-size: 24px 24px;
}
header[data-testid="stHeader"] {
  height: 2.35rem !important;
  min-height: 2.35rem !important;
}
[data-testid="stMain"] {
  scrollbar-gutter: stable;
}
[data-testid="stMainBlockContainer"] {
  max-width: 1260px;
  padding-top: 1.35rem;
  padding-bottom: 4rem;
}
[data-testid="stSidebar"] {
  background: #294b38;
  border-right: 5px solid #6b4f31;
}
[data-testid="stSidebar"] > div:first-child {
  background-image: repeating-linear-gradient(0deg, rgba(255,255,255,.025) 0 3px,
                                               transparent 3px 31px);
}
[data-testid="stSidebar"] * { color: #f9eed1; }
[data-testid="stSidebar"] [role="radiogroup"] label {
  border: 2px solid transparent;
  border-radius: 8px;
  margin: 3px 0;
  padding: 3px 6px;
  transition: background .15s ease, transform .15s ease;
}
[data-testid="stSidebar"] [role="radiogroup"] label:hover {
  background: #426a4b;
  transform: translateX(3px);
}
[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) {
  background: #f6e7bd;
  border-color: #b4874c;
  box-shadow: 3px 3px 0 #1c3629;
}
[data-testid="stSidebar"] [role="radiogroup"] label:has(input:checked) * {
  color: var(--cd-forest) !important;
  font-weight: 800;
}
.cd-logo {
  border: 3px solid #cba15e;
  background: #68492d;
  box-shadow: 4px 4px 0 #193525;
  border-radius: 7px;
  padding: 14px 12px 11px;
  margin: 4px 1px 20px;
  text-align: center;
}
.cd-logo-main { color: #fff2ca; font-size: 1.35rem; font-weight: 900; letter-spacing: .045em; }
.cd-logo-sub { color: #f0d39b; font-size: .73rem; letter-spacing: .2em; margin-top: 3px; }
.cd-banner {
  position: relative;
  overflow: hidden;
  border: 3px solid var(--cd-wood-dark);
  border-radius: 11px;
  background: #ffedc5;
  background-image: repeating-linear-gradient(90deg, rgba(134, 99, 56, .045) 0 2px,
                                              transparent 2px 36px);
  box-shadow: 5px 6px 0 #a77e4a;
  box-sizing: border-box;
  height: clamp(178px, calc(23vh + 38px), 228px);
  padding: 14px 23px;
  margin: 3px 5px 24px 1px;
}
.cd-banner-copy { position: relative; z-index: 1; display: flex; flex-direction: column;
                  justify-content: center; width: 63%; height: 100%; }
.cd-eyebrow { color: #7a542e; font-size: .76rem; font-weight: 800; letter-spacing: .16em; }
.cd-slogan-line { display: flex; align-items: baseline; gap: 14px; white-space: nowrap; }
.cd-banner-title { color: #355638; font-size: clamp(1.3rem, 3vw, 2.1rem); font-weight: 900;
                   line-height: 1.22; margin: 8px 0 4px; text-shadow: 2px 2px 0 #ffefca; }
.cd-banner-encourage { color: #a45739; font-size: clamp(.9rem, 1.5vw, 1.2rem); font-weight: 900; }
.cd-scene { position: absolute; right: 0; top: 0; width: 36%; height: 100%;
            overflow: hidden; border-left: 3px solid var(--cd-wood-dark); background: #6fa33b; }
.cd-scene img { position: absolute; image-rendering: pixelated; object-fit: contain;
  filter: drop-shadow(2px 3px 0 rgba(45, 44, 28, .35)); }
.cd-scene-terrain { inset: 0; width: 100%; height: 100%; object-fit: cover !important;
                    object-position: center 43%; z-index: 0; filter: none !important; }
.cd-scene-flora { position: absolute; z-index: 1; height: auto;
                  overflow: visible; shape-rendering: crispEdges;
                  filter: drop-shadow(1px 2px 0 rgba(45, 44, 28, .28)); }
.cd-scene-building { right: 1%; bottom: 2%; width: auto; height: 92%;
                     max-width: 56%; max-height: 94%; z-index: 2; }
.cd-scene-animal { z-index: 3; height: auto; max-width: 18%; }
.cd-scene-animal[data-animal="krobus"] {
  filter: drop-shadow(1px 0 #f4e6ac) drop-shadow(-1px 0 #f4e6ac)
          drop-shadow(0 1px #f4e6ac) !important;
}
h1, h2, h3 { color: var(--cd-forest) !important; letter-spacing: .01em; }
h1 { font-weight: 900 !important; text-shadow: 2px 2px 0 #d4bd88; }
h3 { border-left: 7px solid #8faa69; padding-left: 12px; margin-top: 1.25rem !important; }
[data-testid="stElementContainer"]:has([data-testid="stHeading"] h3) {
  margin-bottom: 11px;
}
[data-testid="stMetric"] {
  background: var(--cd-paper);
  border: 3px solid var(--cd-wood);
  border-radius: 9px;
  box-shadow: 4px 5px 0 var(--cd-wood-dark);
  padding: 13px 16px;
  min-height: 104px;
}
[data-testid="stMetricLabel"] { color: #705534; font-weight: 800; }
[data-testid="stMetricValue"] { color: var(--cd-forest); font-weight: 900; }
.cd-position-company {
  display: inline-block;
  padding: 4px 10px;
  border-left: 5px solid #6b8f4f;
  background: #e8f0d3;
  color: #294b38;
  font-size: 1.08rem;
  font-weight: 900;
}
.cd-position-title { margin: 2px 0 7px; color: #294b38; font-size: 1.42rem;
  font-weight: 900; line-height: 1.3; text-align: left; }
.cd-company-heading { display: flex; flex-wrap: wrap; align-items: baseline; gap: 5px 13px;
  margin: 0 0 6px; color: #294b38; }
.cd-company-heading strong { font-size: 1.52rem; font-weight: 900; line-height: 1.28; }
.cd-company-english { font-size: .91rem; font-weight: 700; color: #607765; }
.cd-company-industry { margin-left: auto; padding: 4px 11px; border-radius: 999px;
  background: #edf0d9; border: 1px solid #cad9b4; color: #385942;
  font-size: .84rem; font-weight: 750; line-height: 1.25; }
.cd-company-city { display: flex; flex-wrap: wrap; gap: 4px 10px; margin: 0;
  color: #695f4b; font-size: .88rem; line-height: 1.5; }
.cd-company-city span:first-child { color: #8a805f; font-size: .79rem; font-weight: 800;
  letter-spacing: .04em; }
.cd-submission-card {
  background: #fff9ea; border: 1px solid #cdbb96; border-left: 5px solid #78955d;
  border-radius: 9px; padding: 14px 18px; margin: 9px 0 16px;
  color: #294b38; line-height: 1.45;
}
.cd-submission-title { font-size: 1.05rem; font-weight: 900; margin-bottom: 10px; }
.cd-submission-meta { display: flex; flex-wrap: wrap; gap: 10px 30px;
  padding-bottom: 10px; border-bottom: 1px solid #e3d6b7; }
.cd-submission-meta > div { display: flex; flex-wrap: wrap; gap: 4px 10px; }
.cd-submission-meta span, .cd-submission-note span { color: #77684f; font-weight: 800; }
.cd-submission-meta strong { color: #294b38; }
.cd-submission-note { padding-top: 10px; }
.cd-submission-note p { margin: 4px 0 0; overflow-wrap: anywhere; }
.cd-overview-position { padding: 4px 0 8px; border-bottom: 1px solid #d7c7a2; }
.cd-overview-position-heading { display: flex; flex-wrap: wrap; align-items: center;
  gap: 6px 9px; color: #314f3b; }
.cd-overview-position-heading strong { font-size: 1.04rem; }
.cd-overview-application-mark { display: inline-block; padding: 2px 8px;
  border: 1px solid; border-radius: 999px; font-size: .77rem; font-weight: 800; }
.cd-overview-application-mark-1 { background: #f8edd4; color: #74551f; border-color: #d6bd82; }
.cd-overview-application-mark-2 { background: #e8e5dd; color: #615b50; border-color: #c6c0b2; }
.cd-overview-position-detail { display: flex; flex-wrap: wrap; gap: 3px 18px;
  padding: 5px 0 0; color: #294b38; font-size: .99rem; line-height: 1.4; font-weight: 750; }
.cd-overview-position-detail span { color: #746851; font-size: .88rem; font-weight: 600; }
[class*="st-key-company_list_heading"] { margin-bottom: 8px !important; }
[class*="st-key-company_card_"] {
  padding: 17px 20px 14px !important;
  border: 1px solid #cfbd94 !important;
  border-left: 5px solid #6f915b !important;
  border-radius: 12px !important;
  background: linear-gradient(120deg, #fffaf0, #f9f1dc) !important;
  box-shadow: 0 3px 0 #e0cfaa !important;
  gap: .62rem !important;
}
[class*="st-key-company_card_"]:hover { border-color: #95ae79 !important;
  box-shadow: 0 4px 0 #d5c79e !important; }
[class*="st-key-company_card_"] [data-testid="stMarkdownContainer"]:has(.cd-company-heading),
[class*="st-key-company_card_"] [data-testid="stMarkdownContainer"]:has(.cd-company-city) {
  margin-bottom: 0 !important;
}
[class*="st-key-company_card_"] [data-testid="stMarkdownContainer"] p { margin: 0; }
[class*="st-key-company_card_"] [class*="st-key-open_company_catalog_"] button,
[class*="st-key-company_card_"] [class*="st-key-edit_company_card_"] button {
  min-height: 2.35rem; padding: 5px 12px; border-radius: 7px;
  font-size: .91rem; box-shadow: none; transition: background .12s ease, border-color .12s ease;
}
[class*="st-key-company_card_"] [class*="st-key-open_company_catalog_"] button {
  color: #fff9e9; background: #476d48; border: 1px solid #476d48;
}
[class*="st-key-company_card_"] [class*="st-key-open_company_catalog_"] button:hover {
  color: white; background: #365a3d; box-shadow: none; transform: none;
}
[class*="st-key-company_card_"] [class*="st-key-edit_company_card_"] button {
  color: #4c624d; background: #fffaf0; border: 1px solid #a6b294;
}
[class*="st-key-company_card_"] [class*="st-key-edit_company_card_"] button:hover {
  background: #eef0d9; box-shadow: none; transform: none;
}
.cd-page-number { padding: 9px 0; text-align: center; color: #705534; font-weight: 800; }
[data-testid="stAlert"] {
  background: #fff8e6;
  border: 2px solid #c6a56d;
  border-radius: 8px;
  box-shadow: 3px 3px 0 #e2cf9b;
}
[data-testid="stExpander"] {
  background: var(--cd-paper);
  border: 2px solid var(--cd-line);
  border-radius: 9px;
  box-shadow: 3px 3px 0 #e5d1a4;
  overflow: hidden;
}
[data-testid="stExpander"] summary { font-weight: 800; color: var(--cd-forest); }
.st-key-application_list_panel [data-testid="stExpander"] { overflow: visible; }
.cd-compare-scroll, .cd-import-list-scroll { overflow: auto; max-width: 100%; margin: 8px 0 14px; }
.cd-import-list-scroll { max-height: 240px; overflow-x: hidden; overflow-y: auto;
                          border: 1px solid #c7ae7e; border-radius: 7px; }
.cd-compare-table { width: 100%; border-collapse: separate; border-spacing: 0;
                    background: #fffaf0; color: #2f4934; font-size: .88rem; }
.cd-compare-table th, .cd-compare-table td { padding: 8px 10px; border-bottom: 1px solid #dcc89f;
                                               text-align: left; vertical-align: top; min-width: 110px;
                                               overflow-wrap: anywhere; white-space: normal; }
.cd-compare-table thead th { position: sticky; top: 0; z-index: 1; background: #e9d5aa;
                             color: #294d36; font-weight: 800; }
.cd-compare-table tbody th { background: #f5ebd5; font-weight: 800; min-width: 100px; }
.cd-compare-table tr.cd-compare-difference td { background: #fff0cf; }
.cd-simple-table { width: 100%; table-layout: fixed; border-collapse: collapse;
                   background: #fffaf0; color: #2f4934; font-size: .86rem; }
.cd-simple-table th, .cd-simple-table td { padding: 8px 9px; border-bottom: 1px solid #dcc89f;
                                           text-align: left; overflow-wrap: anywhere; white-space: normal; }
.cd-timeline-table { max-height: none !important; overflow: visible; }
.cd-timeline-table .cd-history-row { cursor: default; }
.cd-timeline-table .cd-history-row:hover td,
.cd-timeline-table .cd-history-row:focus td { background: #f4edcf; }
.cd-timeline-table .cd-history-row:focus { outline: 2px solid #5f8b50; }
.cd-timeline-table .cd-history-row td:last-child { position: relative; }
.cd-history-tooltip {
  display: none;
  position: absolute;
  z-index: 20;
  top: calc(100% + 3px);
  right: 0;
  width: max-content;
  max-width: min(430px, 82vw);
  padding: 10px 13px;
  background: #fff9e9;
  border: 2px solid #91683c;
  border-radius: 8px;
  box-shadow: 4px 4px 0 #684826;
  color: #294534;
  line-height: 1.55;
  text-align: left;
  pointer-events: none;
  white-space: normal;
}
.cd-timeline-table .cd-history-row:hover td:last-child,
.cd-timeline-table .cd-history-row:focus td:last-child { z-index: 5; }
.cd-timeline-table .cd-history-row:hover .cd-history-tooltip,
.cd-timeline-table .cd-history-row:focus .cd-history-tooltip { display: block; }
[class*="st-key-application_list_panel"] { gap: .35rem !important; }
[class*="st-key-application_entry_"] {
  position: relative; gap: 0 !important; border-bottom: 1px solid #c9b991;
}
[class*="st-key-application_entry_"]:hover,
[class*="st-key-application_entry_"]:focus-within { z-index: 5; }
[class*="st-key-application_entry_"] button p {
  margin: 0; font-size: 1.08rem; line-height: 1.3; font-weight: 750;
}
[class*="st-key-application_entry_"] > [data-testid="stElementContainer"]:has(.cd-history-tooltip) {
  height: 0 !important; overflow: visible;
}
[class*="st-key-application_entry_"] .cd-history-tooltip {
  top: calc(100% - 3px); left: 12px; right: auto;
}
[class*="st-key-application_entry_"]:hover .cd-history-tooltip,
[class*="st-key-application_entry_"]:focus-within .cd-history-tooltip { display: block; }
.cd-simple-table thead th { position: sticky; top: 0; background: #e9d5aa;
                            color: #294d36; font-weight: 800; }
[data-testid="stTabs"] [role="tablist"] { border-bottom: 3px solid #b38c55; gap: 6px; }
[data-testid="stTabs"] [role="tab"] {
  background: #e7d2a5;
  border: 2px solid #a57b49;
  border-radius: 7px 7px 0 0;
  color: #664927;
  font-weight: 800;
  padding: 8px 16px;
}
[data-testid="stTabs"] [role="tab"][aria-selected="true"] {
  background: var(--cd-paper);
  color: var(--cd-forest);
  border-bottom-color: var(--cd-paper);
}
div.stButton > button, div.stDownloadButton > button, div.stFormSubmitButton > button {
  border: 2px solid #6f8d4e;
  border-radius: 7px;
  background: #e9f1d0;
  color: #2d5737;
  font-weight: 800;
  box-shadow: 3px 3px 0 #a2b87b;
  transition: transform .12s ease, box-shadow .12s ease, background .12s ease;
}
div.stButton > button:hover, div.stDownloadButton > button:hover, div.stFormSubmitButton > button:hover {
  background: #f4f8dc;
  color: #244a30;
  transform: translate(-1px, -1px);
  box-shadow: 4px 4px 0 #8ca96a;
}
div.stButton > button:active, div.stDownloadButton > button:active, div.stFormSubmitButton > button:active {
  transform: translate(2px, 2px);
  box-shadow: 1px 1px 0 #8ca96a;
}
[class*="st-key-application_entry_"] div.stButton > button {
  width: 100%; min-height: 2.85rem; padding: 9px 12px; white-space: normal;
  background: transparent !important; border: 0 !important;
  border-radius: 5px !important; box-shadow: none !important;
  color: #294b38 !important; transform: none !important;
  justify-content: flex-start !important; text-align: left !important;
}
[class*="st-key-application_entry_"] div.stButton > button:hover,
[class*="st-key-application_entry_"] div.stButton > button:focus-visible {
  background: #efe9d6 !important;
}
[class*="st-key-application_entry_"] button > div,
[class*="st-key-application_entry_"] button > div > span {
  justify-content: flex-start !important;
}
[class*="st-key-application_entry_"] button [data-testid="stMarkdownContainer"],
[class*="st-key-application_entry_"] button p { text-align: left !important; }
[class*="st-key-delete_position_card_"] button,
[class*="st-key-delete_recruitment_event_"] button,
[class*="st-key-confirm_delete_event_"] button {
  background: #f9ded8 !important;
  border-color: #b34c3f !important;
  color: #952f26 !important;
  box-shadow: 3px 3px 0 #c68678 !important;
}
[class*="st-key-delete_position_card_"] button:hover,
[class*="st-key-delete_recruitment_event_"] button:hover,
[class*="st-key-confirm_delete_event_"] button:hover {
  background: #ffe9e2 !important;
  color: #7c261e !important;
}
[data-testid="stWidgetLabel"] {
  border-left: 4px solid #89a963;
  padding-left: 9px;
  color: #284b34;
  font-weight: 800;
}
[data-testid="stWidgetLabel"] p { color: #284b34; font-weight: 800; font-size: 1rem; }
[class*="st-key-overview_"][class*="_open"] button {
  display: block;
  min-height: 104px;
  padding: 13px 16px;
  text-align: left;
  background: var(--cd-paper);
  border: 3px solid var(--cd-wood);
  border-radius: 9px;
  box-shadow: 4px 5px 0 var(--cd-wood-dark);
  color: #705534;
}
[class*="st-key-overview_"][class*="_open"] button:hover {
  background: #f9f0d6;
  border-color: #5f8b50;
  color: #705534;
  box-shadow: 4px 5px 0 var(--cd-wood-dark);
}
[class*="st-key-overview_"][class*="_open"] button p {
  margin: 0;
  font-size: 1rem;
  font-weight: 800;
  line-height: 1.25;
}
[class*="st-key-overview_"][class*="_open"] button strong {
  display: block;
  margin-top: 7px;
  color: var(--cd-forest);
  font-size: 2.3rem;
  line-height: 1;
}
button[kind="primary"], button[data-testid="stBaseButton-primary"] {
  background: #5f8b50 !important;
  border-color: #2e603d !important;
  color: #fffbe8 !important;
  box-shadow: 3px 3px 0 #2b5638 !important;
}
button[kind="primary"]:hover, button[data-testid="stBaseButton-primary"]:hover {
  background: #739f60 !important;
}
div[data-baseweb="input"] > div, div[data-baseweb="textarea"] > div,
div[data-baseweb="select"] > div, textarea {
  background: #fffdf4 !important;
  border-color: #bb9d6a !important;
  border-radius: 7px !important;
}
div[data-baseweb="input"]:focus-within > div, div[data-baseweb="textarea"]:focus-within > div,
div[data-baseweb="select"]:focus-within > div {
  box-shadow: 0 0 0 2px #91ac6a !important;
}
[data-testid="stDataFrame"] {
  border: 3px solid #b79a6a;
  border-radius: 8px;
  box-shadow: 3px 3px 0 #e0cb9b;
  overflow: hidden;
}
[data-testid="stCaptionContainer"] { color: var(--cd-muted); }
.cd-footer { margin-top: 3rem; padding: 12px 0; border-top: 2px dashed #b7a173;
             color: #697b62; font-size: .8rem; text-align: center; }
@media (max-width: 1100px) {
  .cd-scene-animal[data-extra="true"] { display: none; }
}
@media (max-width: 750px) {
  [data-testid="stMainBlockContainer"] { padding-left: 1rem; padding-right: 1rem; }
  .cd-banner { height: clamp(164px, calc(25vh + 38px), 203px); padding: 9px 10px; margin-bottom: 18px; }
  .cd-banner-copy { width: 58%; justify-content: flex-start; }
  .cd-eyebrow { font-size: .54rem; letter-spacing: .03em; }
  .cd-slogan-line { gap: 5px; }
  .cd-banner-title { font-size: clamp(.72rem, 2.7vw, 1.2rem); margin-top: 8px; }
  .cd-banner-encourage { font-size: clamp(.63rem, 2.1vw, .86rem); }
  .cd-scene { width: 41%; }
  .cd-scene-building { max-height: 90%; }
  .cd-scene-flora { transform: scale(.78); transform-origin: top left; }
  .cd-scene-animal { transform: scale(.72); transform-origin: bottom left; }
}
</style>
"""


def inject_theme() -> None:
    st.markdown(CSS, unsafe_allow_html=True)


def banner(page: str) -> None:
    page = html.escape(page)
    st.markdown(
        f'<div class="cd-banner"><div class="cd-banner-copy">'
        f'<div class="cd-eyebrow">CAREERDESK · 求职旅程手账</div>'
        f'<div class="cd-slogan-line"><div class="cd-banner-title">我一定会得到想要的工作</div>'
        f'<div class="cd-banner-encourage">加油老乡</div></div>'
        f'</div>{page_scene_html(page)}</div>',
        unsafe_allow_html=True,
    )


def sidebar_brand() -> None:
    st.sidebar.markdown(
        '<div class="cd-logo"><div class="cd-logo-main">🌾 CareerDesk</div>'
        '<div class="cd-logo-sub">个人求职工作台</div></div>',
        unsafe_allow_html=True,
    )


def footer() -> None:
    st.markdown('<div class="cd-footer">CareerDesk · 在自己的电脑上，认真记录每一步</div>',
                unsafe_allow_html=True)
