"""Optional V2.4 browser acceptance against the real authenticated Python API."""
import json
import os
from pathlib import Path
import secrets
import shutil
import sys
import tempfile
import threading

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from blueprint_library.server import initialize_library, make_server
from blueprint_library.store import Store
from blueprint_library.studio import Studio


def main():
    from playwright.sync_api import sync_playwright
    output = Path(sys.argv[1] if len(sys.argv)>1 else '/tmp/blueprint-v24-browser')
    output.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='blueprint-studio-browser-') as temp:
        password = secrets.token_urlsafe(24)
        data = Path(temp)/'private'
        initialize_library(data, 'owner', password, company_profile={'name':'Blueprint Studio'})
        store = Store(data); studio = Studio(store)
        project = studio.create({'kind':'project','title':'整理新季度網站','data':{'stage':'development','progress':40,'summary':'示範內容：保持簡單清楚的網站。','milestones':[{'title':'確認內容','done':True},{'title':'檢查手機版','done':False}]}})['record']
        reference=store.create_item({'kind':'link','title':'學習參考（示範）','url':'https://example.com/learning'})
        document_bytes='# 保持簡潔\n沿用白色與淺灰色。'.encode()
        document=store.add_file('設計方向（示範）.md', document_bytes)
        integration=studio.create({'kind':'integration','title':'保留的接入設定（示範）','data':{'category':'model','provider':'示範服務','enabled':False}})['record']
        studio.create({'kind':'inbox','title':'整理這次會議的重點','data':{'source':'示範內容','content':'先確定下一步，再分配工作。'}})
        studio.save_user({'username':'staff','role':'member','password':'test-member-password'})
        member_studio = Studio(store, {'username':'staff','role':'member','name':'職員'})
        member_studio.save_preferences({'expected_version':0,'data':{'groups':[],'hidden_groups':['work','specialists','company','inspiration','topics']}})
        server=make_server(data);threading.Thread(target=server.serve_forever,daemon=True).start()
        try:
            with sync_playwright() as p:
                executable=os.environ.get('BLUEPRINT_TEST_BROWSER') or shutil.which('chromium')
                browser=p.chromium.launch(**({'executable_path':executable} if executable else {}),headless=True,args=['--no-sandbox','--disable-dev-shm-usage'])
                context=browser.new_context(viewport={'width':1440,'height':1050},http_credentials={'username':'owner','password':password})
                page=context.new_page();errors=[];requests=[]
                page.on('pageerror',lambda error:errors.append(str(error)))
                page.on('request',lambda request:requests.append(request.url))
                base=f'http://127.0.0.1:{server.server_port}'
                page.goto(base);page.wait_for_function('() => BlueprintStudio.isReady()')
                page.wait_for_selector('#workspace-board[style]')
                page.wait_for_function("() => document.querySelector('#workspace-sync').dataset.state === 'ready'")
                assert page.locator('#studio-navigation .studio-primary .studio-nav-label').all_text_contents()==['總覽','工作','AI','資料']
                assert page.locator('#legacy-navigation').is_hidden()
                assert page.locator('#studio-navigation [data-studio-route^="specialist/"]').count()==0

                def go(route):
                    page.evaluate('(route) => BlueprintStudio.navigate(route)',route)
                def open_settings(target=page):
                    target.get_by_role('button',name='設定',exact=True).click()
                    target.wait_for_selector('#settings-dialog[open]')
                    assert target.locator('dialog[open]').count()==1
                def assert_no_connection_wizard(target=page):
                    assert target.locator('[data-connection-open], #connection-dialog, #connection-settings-open').count()==0
                    assert target.locator('script[src$="/connection.js"]').count()==0
                    assert target.evaluate("() => typeof window.BlueprintConnection === 'undefined'")
                def visible_widgets():
                    return page.locator('#workspace-board [data-widget]:visible').evaluate_all('(nodes) => nodes.map(node => node.dataset.widget)')
                assert_no_connection_wizard()
                assert page.locator('#collaboration-orientation, [data-collaboration-route]').count()==0
                assert page.locator('[data-widget=intro-title], [data-widget=intro-description], [data-widget=connection]').count()==0
                assert visible_widgets()==['heading']
                assert page.locator('#edit-board').is_visible()
                assert page.locator('#board-editor').is_hidden()
                assert page.locator('#workspace-sync').inner_text()==''
                page.screenshot(path=str(output/'studio-overview-minimal.png'),full_page=True)

                # The footer has one settings entry. Secondary actions keep their
                # own destinations without leaving overlapping modal dialogs.
                footer=page.locator('.sidebar-bottom')
                # This ordinary host has no managed installer. Its hidden update
                # entry is tested separately on a supported managed fixture.
                assert footer.locator('button:visible, a:visible, [role=button]:visible').evaluate_all('(nodes) => nodes.map(node => node.id)')==['settings-button']
                assert footer.locator('#installer-entry').is_hidden()
                assert footer.locator('#settings-button').inner_text().strip()=='設定'
                assert footer.locator('#version').is_visible()
                assert footer.locator('#version').inner_text().strip()
                assert footer.locator('#help-button, #studio-account, [data-connection-open], [data-studio-route=archived]').count()==0
                open_settings()
                page.locator('#settings-dialog #help-button').click()
                page.wait_for_selector('#help-dialog[open]')
                assert page.locator('#settings-dialog').is_hidden()
                assert page.locator('dialog[open]').count()==1
                page.locator('#help-dialog [data-close]').click()
                open_settings()
                page.locator('#settings-dialog #studio-account').click()
                page.wait_for_selector('#studio-panel h1:text-is("帳戶")')
                assert page.evaluate('location.hash')=='#/account'
                assert page.locator('#settings-dialog').is_hidden()
                assert page.locator('dialog[open]').count()==0
                go('connections')
                page.wait_for_selector('#studio-panel h1:text-is("舊接入紀錄")')
                assert page.locator('#studio-panel h2:text-is("保留的接入設定（示範）")').is_visible()
                assert page.locator('#studio-panel .studio-card button').all_text_contents()==['查看紀錄']
                assert page.locator('#studio-panel .studio-card input, #studio-panel .studio-card select, #studio-panel .studio-card textarea, #studio-panel .studio-tabs').count()==0
                assert page.locator('#studio-section-navigation [data-studio-route=connections]').count()==0
                assert_no_connection_wizard()
                assert page.request.get(base+'/api/studio/records/'+integration['id']).json()['record']['version']==integration['version']
                go('overview')

                # Overview visibility changes display only. An optional real-data
                # card can be enabled and reopened without changing stored records.
                page.locator('#edit-board').click()
                page.wait_for_selector('#board-visible-fields')
                assert page.locator('#board-visible-fields [data-board-visible=heading]').is_checked()
                assert page.locator('#board-visible-fields [data-board-visible=heading]').is_disabled()
                assert page.locator('#board-visible-fields input:checked').count()==1
                page.locator('#board-visible-fields [data-board-visible=recent]').check()
                page.locator('#edit-board').click()
                assert set(visible_widgets())=={'heading','recent'}
                page.wait_for_selector('#dashboard-library-slot .item-title:text-is("設計方向（示範）.md")')
                page.reload();page.wait_for_function('() => BlueprintStudio.isReady()')
                assert set(visible_widgets())=={'heading','recent'}
                assert set(page.evaluate("() => JSON.parse(localStorage.getItem('blueprint-workspace-v2')).visible"))=={'heading','recent'}
                page.screenshot(path=str(output/'studio-overview-with-real-data.png'),full_page=True)
                page.locator('#edit-board').click()
                page.locator('#board-visible-fields [data-board-visible=recent]').uncheck()
                page.locator('#edit-board').click()
                assert visible_widgets()==['heading']
                assert page.request.get(base+'/api/files/'+document['id']).body()==document_bytes
                assert page.request.get(base+'/api/items/'+reference['id']).json()['item']['url']==reference['url']
                assert page.request.get(base+'/api/studio/records/'+project['id']).json()['record']['title']==project['title']

                page.locator('#studio-navigation [data-group=work]').click()
                page.wait_for_selector('#studio-panel h1:text-is("項目")')
                assert page.locator('#studio-section-navigation .studio-section-link').all_text_contents()==['項目','收件匣','待辦','工作紀錄','日曆']
                assert page.get_by_role('button',name='整理新季度網站',exact=True).is_visible()
                page.locator('#studio-navigation [data-group=specialists]').click()
                page.wait_for_selector('#skill-workspace[data-skills-ready=true]')
                assert page.locator('#studio-section-navigation .studio-section-link').all_text_contents()==['Skill 與交接','Agent 工作台','工作指令']
                assert_no_connection_wizard()
                page.locator('#studio-navigation [data-group=inspiration]').click()
                page.wait_for_function("() => !document.getElementById('library-panel').hidden && document.getElementById('studio-panel').hidden")
                assert page.locator('#studio-section-navigation .studio-section-link').all_text_contents()==['文件','收藏','主題','釘選','已封存']
                page.wait_for_selector('#item-grid .item-title:text-is("設計方向（示範）.md")')
                assert_no_connection_wizard()
                page.locator('#studio-section-navigation [data-studio-route=archived]').click()
                page.wait_for_selector('#studio-panel h1:text-is("已封存")')
                assert page.evaluate('location.hash')=='#/archived'
                page.locator('#studio-navigation [data-group=overview]').click()
                page.wait_for_function("() => !document.getElementById('dashboard-panel').hidden")
                page.screenshot(path=str(output/'studio-overview.png'),full_page=True)
                def submit():
                    page.locator('#studio-form button[type=submit]').click()
                    page.wait_for_function("() => !document.getElementById('studio-editor').open")
                go('projects');page.get_by_role('button',name='新增項目',exact=True).click()
                page.locator('#studio-record-title').fill('瀏覽器建立項目')
                page.locator('[data-field=summary]').fill('實際保存及讀回')
                page.locator('[data-field=progress]').fill('25');submit()
                page.wait_for_selector('#studio-panel h1:text-is("瀏覽器建立項目")')
                assert studio.list('project',q='瀏覽器')['records'][0]['data']['progress']==25
                # Search includes studio data; Cmd+J is a separate command composer.
                page.keyboard.press('Control+k');page.locator('#command-search').fill('瀏覽器建立')
                page.wait_for_selector('#command-results .command-result')
                page.locator('#command-results .command-result').first.click()
                page.keyboard.press('Control+j');page.locator('#studio-command-input').fill('整理已選定的工作內容')
                page.locator('#studio-command-form button[type=submit]').click()
                page.wait_for_selector('#studio-handoff[open]')
                assert 'blueprint_studio_context' in page.locator('#studio-handoff-text').input_value()
                page.locator('#studio-handoff [data-studio-close]').click()
                assert studio.list('command',q='整理已選定')['records'][0]['data']['state']=='awaiting_ai'
                # The dialog keeps typed text on refresh.
                go('leads');page.get_by_role('button',name='新增潛在客戶',exact=True).click()
                page.locator('#studio-record-title').fill('示範客戶')
                page.locator('[data-field=company]').fill('示範公司')
                page.locator('[data-field=email]').fill('demo@example.com')
                page.evaluate('() => BlueprintStudio.refreshVisible()')
                assert page.locator('[data-field=company]').input_value()=='示範公司'
                submit();page.get_by_role('button',name='建立報價草稿',exact=True).click()
                page.wait_for_selector('#studio-panel h1:text-is("示範客戶 · 報價")')
                page.get_by_role('button',name='修改',exact=True).click()
                page.wait_for_selector('#studio-editor[open]')
                page.locator('[data-field=lines]').get_by_role('button',name='＋ 加入一項').click()
                page.locator('[data-part=description]').fill('網站服務')
                page.locator('[data-part=unit_price]').fill('1200');submit()
                for label in ['提交確認','確認草稿','記錄已自行發送','記錄已簽署','建立客戶檔案與發票']:
                    page.get_by_role('button',name=label,exact=True).click()
                    page.locator('#studio-action-submit').click()
                    page.wait_for_function("() => !document.getElementById('studio-action-dialog').open || !document.getElementById('studio-action-error').hidden")
                    assert page.locator('#studio-action-error').is_hidden(), (label, page.locator('#studio-action-error').inner_text())
                assert studio.list('invoice')['records'][0]['data']['amount']=='1200.00'
                go('revenue');page.screenshot(path=str(output/'studio-revenue.png'),full_page=True)
                go('topics');page.get_by_role('button',name='新增自訂主題',exact=True).click()
                page.locator('#studio-record-title').fill('網站設計靈感')
                page.locator('#studio-refs').select_option(json.dumps({'kind':'project','id':project['id']},separators=(',',':')))
                submit();page.wait_for_selector('#studio-panel h1:text-is("網站設計靈感")')
                assert page.get_by_role('button',name='整理新季度網站',exact=True).is_visible()
                # Legacy entry forms synchronize their studio mirrors without polling.
                go('inspirations');page.locator('#add-item').click();page.locator('#add-menu [data-action=link]').click()
                page.locator('#link-url').fill('https://example.com/new-reference');page.locator('#link-title').fill('立即顯示的新收藏')
                page.locator('#link-form button[type=submit]').click()
                page.wait_for_selector('#studio-panel .studio-record-title:text-is("立即顯示的新收藏")')
                assert studio.list('inspiration',q='立即顯示的新收藏')['total']==1
                page.locator('#add-item').click();page.locator('#add-menu [data-action=collection]').click()
                page.locator('#collection-name').fill('立即顯示的新主題');page.locator('#collection-form button[type=submit]').click()
                page.wait_for_selector('#studio-panel h1:text-is("立即顯示的新主題")')
                assert studio.list('topic',q='立即顯示的新主題')['total']==1
                # Every top-level and child page actually renders.
                for route in ['inbox','projects','documents','reports','specialist/specialist-website','specialist/specialist-whatsapp','specialist/specialist-email','specialist/specialist-video','templates','connections','leads','quotes','customers','feedback','team','suppliers','revenue','costs','inspirations','inspirations/web','inspirations/components','inspirations/ai','inspirations/brand','inspirations/business','inspirations/learning','topics','archived','account']:
                    go(route)
                    assert page.locator('#notice.error:visible').count()==0, (route,page.locator('#notice').inner_text())
                go('specialist/specialist-email')
                for tab in ['設定','對話紀錄','測試','數據','總覽']:
                    page.locator('.studio-tabs').get_by_role('button',name=tab,exact=True).click()
                assert '未接入' in page.locator('#studio-panel').inner_text()
                page.screenshot(path=str(output/'studio-specialist.png'),full_page=True)
                go('projects');page.screenshot(path=str(output/'studio-projects.png'),full_page=True)
                assert page.locator('.studio-narrow-toggle').count() == 0
                assert not page.locator('body').evaluate("n => n.classList.contains('studio-narrow')")
                sidebar_width = page.locator('#sidebar').evaluate('n => n.getBoundingClientRect().width')
                page.reload();page.wait_for_function('() => BlueprintStudio.isReady()')
                assert page.locator('#sidebar').evaluate('n => n.getBoundingClientRect().width') == sidebar_width
                assert page.locator('.topbar #studio-section-navigation').is_visible()
                assert page.locator('.breadcrumb').count() == 0
                assert page.locator('#settings-button [data-icon=settings] .ui-icon').is_visible()
                open_settings()
                page.locator('#settings-dialog [data-close]').click()
                page.set_viewport_size({'width':390,'height':844})
                page.locator('#mobile-menu').wait_for(state='visible')
                assert page.locator('#studio-mobile-command').is_visible()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.locator('#mobile-menu').click()
                page.locator('#sidebar.open').wait_for(state='visible')
                assert page.locator('#sidebar.open').is_visible()
                assert page.locator('#mobile-menu').get_attribute('aria-expanded')=='true'
                page.screenshot(path=str(output/'studio-mobile.png'),full_page=True)
                page.locator('#sidebar-backdrop').click(position={'x':350,'y':200})
                assert page.locator('#sidebar.open').count()==0
                page.locator('#mobile-menu').click()
                open_settings()
                assert page.locator('#sidebar.open').count()==0
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                settings_box=page.locator('#settings-dialog').bounding_box()
                assert settings_box['x']>=0 and settings_box['x']+settings_box['width']<=390, settings_box
                page.locator('#settings-dialog [data-close]').click()
                # Old URLs route to the retained daily-work view.
                page.goto(base+'/?view=workdays');page.wait_for_function("() => BlueprintStudio.isReady() && location.hash === '#/overview/workdays'")
                page.locator('#workdays-panel').wait_for(state='visible')
                # An obsolete deep link must not trap the entire workbench behind retry.
                page.set_viewport_size({'width':1440,'height':1050})
                page.goto(base+'/#/record/missing');page.wait_for_function('() => BlueprintStudio.isReady()')
                assert page.evaluate('() => document.body.dataset.workspaceState')=='ready'
                assert page.evaluate('() => location.hash')=='#/overview'
                assert page.locator('#studio-navigation').is_visible()
                assert '原本的頁面' in page.locator('#notice').inner_text()
                member_context=browser.new_context(viewport={'width':1440,'height':900},http_credentials={'username':'staff','password':'test-member-password'})
                member=member_context.new_page();member.goto(base);member.wait_for_function('() => BlueprintStudio.isReady()')
                assert member.locator('#studio-navigation .studio-primary .studio-nav-label').all_text_contents()==['總覽','工作','AI','資料']
                open_settings(member)
                assert member.locator('#settings-dialog [data-admin-only]:visible').count()==0
                assert member.locator('#edit-company-profile').is_hidden()
                member.locator('#studio-account').click()
                member.wait_for_selector('#studio-panel h1:text-is("帳戶")')
                assert member.evaluate('location.hash')=='#/account'
                assert '登入名稱：staff' in member.locator('#studio-panel').inner_text()
                assert '角色：員工' in member.locator('#studio-panel').inner_text()
                assert member.locator('#settings-dialog').is_hidden()
                assert member.get_by_role('button',name='管理團隊登入',exact=True).count()==0
                assert_no_connection_wizard(member)
                member.locator('#studio-navigation [data-group=work]').click()
                assert member.locator('[data-studio-route=revenue]').count()==0
                assert member.locator('[data-studio-route=team]').count()==0
                invoice=studio.list('invoice')['records'][0]
                response=member.request.get(base+'/api/studio/records/'+invoice['id']);assert response.status==403
                assert member.request.get(base+'/api/studio/records?kind=invoice').status==403
                assert member.request.get(base+'/api/studio/users').status==403
                assert member.request.get(base+'/api/studio/records/'+integration['id']).status==403
                member.goto(base+'/?startup=restricted#/revenue');member.wait_for_function('() => BlueprintStudio.isReady()')
                assert member.evaluate('() => document.body.dataset.workspaceState')=='ready'
                assert member.evaluate('() => location.hash')=='#/overview'
                assert not any(url.split('?')[0].endswith('/connection.js') for url in requests), requests
                assert_no_connection_wizard()
                assert not errors, errors
                browser.close()
        finally:
            server.shutdown();server.server_close()
    print('Workbench browser checks passed: minimal overview/optional real cards, no AI wizard/orientation, retained records and integration deep links, settings-only footer and secondary dialogs, own account/member permissions, archived data tab, CRUD, search, commands, quote-to-invoice, topics, all pages, drafts, stable sidebar width/topbar/mobile, old URLs.')


if __name__=='__main__':main()
