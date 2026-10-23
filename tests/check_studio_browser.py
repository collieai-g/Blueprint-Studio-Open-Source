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
        store.create_item({'kind':'link','title':'學習參考（示範）','url':'https://example.com/learning'})
        store.add_file('設計方向（示範）.md', '# 保持簡潔\n沿用白色與淺灰色。'.encode())
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
                page=context.new_page();errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                base=f'http://127.0.0.1:{server.server_port}'
                page.goto(base);page.wait_for_function('() => BlueprintStudio.isReady()')
                page.wait_for_selector('#workspace-board[style]')
                page.wait_for_function("() => document.querySelector('#workspace-sync').dataset.state === 'ready'")
                assert page.locator('#studio-navigation .studio-primary .studio-nav-label').all_text_contents()==['總覽','工作','AI','資料']
                assert page.locator('#legacy-navigation').is_hidden()
                assert page.locator('#studio-navigation [data-studio-route^="specialist/"]').count()==0

                # Orientation is outside the editable board: its real links
                # explain the two work areas without migrating saved card data.
                orientation=page.locator('#collaboration-orientation')
                assert orientation.is_visible()
                assert orientation.get_attribute('open') is not None
                assert page.locator('#workspace-board #collaboration-orientation').count()==0
                def board_geometry():
                    return page.evaluate('''() => {
                      const board=document.getElementById('workspace-board'),box=board.getBoundingClientRect();
                      return [...board.querySelectorAll('[data-widget]')].map(node=>{
                        const rect=node.getBoundingClientRect();
                        return {id:node.dataset.widget,x:rect.x-box.x,y:rect.y-box.y,w:rect.width,h:rect.height};
                      });
                    }''')
                original_cards=board_geometry()
                assert len(original_cards)==13
                original_preferences=page.evaluate("() => localStorage.getItem('blueprint-workspace-v2')")
                codex=page.locator('#collaboration-codex-pane').bounding_box()
                blueprint=page.locator('#collaboration-blueprint-pane').bounding_box()
                assert abs(codex['y']-blueprint['y'])<2 and codex['x']+codex['width']<=blueprint['x'], (codex,blueprint)
                orientation.locator('[data-collaboration-route=skills]').click()
                page.wait_for_selector('#skill-workspace[data-skills-ready=true]')
                assert page.evaluate('location.hash')=='#/skills'
                page.locator('#studio-navigation [data-group=overview]').click()
                page.wait_for_selector('#collaboration-orientation')
                page.locator('#collaboration-orientation [data-collaboration-route=projects]').click()
                page.wait_for_selector('#studio-panel h1:text-is("項目")')
                assert page.evaluate('location.hash')=='#/projects'
                page.locator('#studio-navigation [data-group=overview]').click()
                page.wait_for_selector('#collaboration-orientation')
                assert board_geometry()==original_cards
                assert page.evaluate("() => localStorage.getItem('blueprint-workspace-v2')")==original_preferences
                page.evaluate('() => window.scrollTo(0,0)')
                page.screenshot(path=str(output/'studio-orientation-desktop.png'),full_page=True)
                page.set_viewport_size({'width':390,'height':844})
                page.wait_for_timeout(200)
                codex=page.locator('#collaboration-codex-pane').bounding_box()
                blueprint=page.locator('#collaboration-blueprint-pane').bounding_box()
                assert abs(codex['x']-blueprint['x'])<2 and codex['y']+codex['height']<=blueprint['y'], (codex,blueprint)
                assert page.evaluate('document.documentElement.scrollWidth<=innerWidth')
                assert page.evaluate("() => localStorage.getItem('blueprint-workspace-v2')")==original_preferences
                page.screenshot(path=str(output/'studio-orientation-mobile.png'),full_page=True)
                page.set_viewport_size({'width':1440,'height':1050})
                page.wait_for_timeout(200)
                assert board_geometry()==original_cards
                assert page.evaluate("() => localStorage.getItem('blueprint-workspace-v2')")==original_preferences

                page.locator('#studio-navigation [data-group=work]').click()
                page.wait_for_selector('#studio-panel h1:text-is("項目")')
                assert page.locator('#studio-section-navigation .studio-section-link').all_text_contents()==['項目','收件匣','待辦','工作紀錄']
                page.locator('#studio-navigation [data-group=specialists]').click()
                page.wait_for_selector('#skill-workspace[data-skills-ready=true]')
                page.locator('#studio-navigation [data-group=inspiration]').click()
                page.wait_for_function("() => !document.getElementById('library-panel').hidden && document.getElementById('studio-panel').hidden")
                assert page.locator('#studio-section-navigation .studio-section-link').all_text_contents()==['文件','收藏','主題','釘選']
                page.locator('#studio-navigation [data-group=overview]').click()
                page.wait_for_function("() => !document.getElementById('dashboard-panel').hidden")
                page.screenshot(path=str(output/'studio-overview.png'),full_page=True)
                def go(route):
                    page.evaluate('(route) => BlueprintStudio.navigate(route)',route)
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
                page.locator('.studio-narrow-toggle').click();page.wait_for_function("() => document.body.classList.contains('studio-narrow')")
                page.reload();page.wait_for_function('() => BlueprintStudio.isReady()');assert page.locator('body').evaluate("n => n.classList.contains('studio-narrow')")
                page.locator('.studio-narrow-toggle').click()
                page.set_viewport_size({'width':390,'height':844});page.wait_for_timeout(100)
                assert page.locator('#studio-mobile-command').is_visible()
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
                page.locator('#mobile-menu').click();assert page.locator('#sidebar.open').is_visible()
                page.screenshot(path=str(output/'studio-mobile.png'),full_page=True)
                page.locator('#sidebar-backdrop').click(position={'x':350,'y':200})
                assert page.locator('#sidebar.open').count()==0
                # Old URLs route to the retained daily-work view.
                page.goto(base+'/?view=workdays');page.wait_for_function("() => location.hash === '#/overview/workdays'")
                assert page.locator('#workdays-panel').is_visible()
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
                member.locator('#studio-navigation [data-group=work]').click()
                assert member.locator('[data-studio-route=revenue]').count()==0
                assert member.locator('[data-studio-route=team]').count()==0
                invoice=studio.list('invoice')['records'][0]
                response=member.request.get(base+'/api/studio/records/'+invoice['id']);assert response.status==403
                member.goto(base+'/?startup=restricted#/revenue');member.wait_for_function('() => BlueprintStudio.isReady()')
                assert member.evaluate('() => document.body.dataset.workspaceState')=='ready'
                assert member.evaluate('() => location.hash')=='#/overview'
                assert not errors, errors
                browser.close()
        finally:
            server.shutdown();server.server_close()
    print('Workbench browser checks passed: orientation CTAs/responsive layout, preserved board/preferences, CRUD, search, commands, quote-to-invoice, topics, all pages, roles, drafts, narrow/sidebar/mobile, old URLs.')


if __name__=='__main__':main()
