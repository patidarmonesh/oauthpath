import tempfile, shutil, os, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from starlette.testclient import TestClient
import app.main as app_main
from app.store import Store

def verify_instance(store, client, label):
    print("=== Testing " + label + " ===")
    guides = store.list_guides()
    guide_map = {g['id']: g for g in guides}
    print("Guides on homepage:", [(g['id'], 'v' + str(g['version'])) for g in guides])

    # Check latest versions
    assert guide_map['slack-e07c7c36']['version'] == 4, "Slack latest version should be 4, got " + str(guide_map['slack-e07c7c36']['version'])
    assert guide_map['gmail-google-b419b8d4']['version'] == 4, "Gmail latest version should be 4, got " + str(guide_map['gmail-google-b419b8d4']['version'])
    assert guide_map['github-2f1b5349']['version'] == 3, "GitHub latest version should be 3, got " + str(guide_map['github-2f1b5349']['version'])

    # Check redirects to v4
    r_slack = client.get('/guides/slack-e07c7c36', follow_redirects=False)
    assert '/versions/4' in r_slack.headers['location'], "Slack redirect location: " + r_slack.headers.get('location', '')
    r_gmail = client.get('/guides/gmail-google-b419b8d4', follow_redirects=False)
    assert '/versions/4' in r_gmail.headers['location'], "Gmail redirect location: " + r_gmail.headers.get('location', '')

    # Check Slack v4 HTML & Markdown
    r_s_html = client.get('/guides/slack-e07c7c36/versions/4')
    assert r_s_html.status_code == 200
    r_s_md = client.get('/guides/slack-e07c7c36/versions/4.md')
    assert r_s_md.status_code == 200
    exact_passage = 'As of July 2026, we require that apps submitting to the Slack Marketplace have at least 10 installations on active workspaces'
    assert exact_passage in r_s_html.text, 'Exact Slack passage missing in v4 HTML'
    assert exact_passage in r_s_md.text, 'Exact Slack passage missing in v4 Markdown'
    assert 'https://docs.slack.dev/changelog/2026/09/01/slack-marketplace-install-requirement/' in r_s_md.text

    # Check Gmail v4 HTML & Markdown
    r_g_html = client.get('/guides/gmail-google-b419b8d4/versions/4')
    assert r_g_html.status_code == 200
    r_g_md = client.get('/guides/gmail-google-b419b8d4/versions/4.md')
    assert r_g_md.status_code == 200
    assert 'restricted' in r_g_md.text.lower()
    assert 'gmail.readonly' in r_g_md.text
    assert 'gmail.send' in r_g_md.text
    assert 'CASA Tier 2' in r_g_md.text
    assert '14 to 56 calendar days' in r_g_md.text or '14' in r_g_md.text

    # Check v3 is still accessible
    assert client.get('/guides/slack-e07c7c36/versions/3').status_code == 200
    assert client.get('/guides/gmail-google-b419b8d4/versions/3').status_code == 200
    assert client.get('/guides/github-2f1b5349/versions/3').status_code == 200

    print("[PASS] " + label + " passed all checks successfully!")

# 1. Test with fresh empty DB
empty_db = tempfile.mktemp(suffix='.db')
try:
    app_main.store = Store(empty_db)
    app_main._import_reviewed_seeds()
    client = TestClient(app_main.app)
    verify_instance(app_main.store, client, 'Fresh Empty Database')
finally:
    if os.path.exists(empty_db):
        os.remove(empty_db)

# 2. Test with copy of current DB
copy_db = tempfile.mktemp(suffix='.db')
try:
    shutil.copy('data/oauthpath.db', copy_db)
    app_main.store = Store(copy_db)
    app_main._import_reviewed_seeds()
    client = TestClient(app_main.app)
    verify_instance(app_main.store, client, 'Copy of Current Database')
finally:
    if os.path.exists(copy_db):
        os.remove(copy_db)

app_main.store = Store(app_main.DATABASE_PATH)
print("\nBOTH TESTS PASSED COMPLETELY!")
