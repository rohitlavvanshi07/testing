# ---------- consent popup helper ----------
def dismiss_consent_popup(driver, logs, max_attempts: int = 3):
    """
    Aggressively tries to dismiss cookie/consent popups.
    Returns True if popup dismissed (or not present), False if still present.
    Non-fatal, logs attempts.
    """
    try:
        for attempt in range(1, max_attempts + 1):
            logs.append(f"Consent dismissal attempt {attempt}/{max_attempts}")
            # Candidate CSS selectors (explicit ones first)
            selectors = [
                "button#onetrust-accept-btn-handler",
                "button[id*='accept']",
                "button[id*='consent']",
                "button[class*='accept']",
                "button[class*='consent']",
                "button[aria-label*='accept']",
                "button[aria-label*='consent']",
                "button[title*='Accept']",
                "button[title*='Consent']",
                "a[id*='accept']",
                "a[class*='accept']",
            ]

            # Try explicit selectors
            for sel in selectors:
                try:
                    elems = driver.find_elements(By.CSS_SELECTOR, sel)
                except Exception:
                    elems = []
                if elems:
                    for el in elems:
                        try:
                            driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                            time.sleep(0.25)
                            el.click()
                            logs.append(f"Clicked consent using selector: {sel}")
                            time.sleep(0.6)
                            return True
                        except Exception:
                            try:
                                driver.execute_script("arguments[0].click();", el)
                                logs.append(f"JS-clicked consent using selector: {sel}")
                                time.sleep(0.6)
                                return True
                            except Exception:
                                continue

            # Text-match fallback across all buttons and links
            try:
                candidates = driver.find_elements(By.TAG_NAME, "button") + driver.find_elements(By.TAG_NAME, "a")
                for el in candidates:
                    try:
                        txt = (el.text or "").strip().lower()
                        if any(k in txt for k in ("accept", "consent", "agree", "allow", "ok", "yes", "manage options", "consent and proceed")):
                            try:
                                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", el)
                                time.sleep(0.2)
                                el.click()
                                logs.append(f"Clicked consent by text match: '{txt[:40]}'")
                                time.sleep(0.6)
                                return True
                            except Exception:
                                try:
                                    driver.execute_script("arguments[0].click();", el)
                                    logs.append(f"JS-clicked consent by text match: '{txt[:40]}'")
                                    time.sleep(0.6)
                                    return True
                                except Exception:
                                    continue
                    except Exception:
                        continue
            except Exception:
                pass

            # Last-resort: remove overlay-like nodes via JS (non-destructive best-effort)
            try:
                js_remove = """
                (function(){
                  const patterns = ['consent','cookie','ccpa','gdpr','banner','overlay','modal','dialog'];
                  let removed = 0;
                  patterns.forEach(p=>{
                    document.querySelectorAll('[id*='+JSON.stringify(p)+'], [class*='+JSON.stringify(p)+'], [role=\"dialog\"]').forEach(el=>{
                      try{ el.style.display='none'; el.remove(); removed++; }catch(e){}
                    });
                  });
                  // Hide obvious full-screen overlays (high z-index & non-transparent)
                  document.querySelectorAll('div').forEach(d=>{
                    try{
                      const s = window.getComputedStyle(d);
                      if (s && s.position && s.zIndex && parseInt(s.zIndex||0) > 1000 && s.backgroundColor && s.backgroundColor !== 'rgba(0, 0, 0, 0)'){
                        d.style.display='none'; d.remove(); removed++;
                      }
                    }catch(e){}
                  });
                  return removed;
                })();
                """
                removed_count = driver.execute_script(js_remove)
                logs.append(f"Attempted overlay removal via JS, removed_count={removed_count}")
                if removed_count > 0:
                    time.sleep(0.5)
                    return True
            except Exception as e:
                logs.append(f"Overlay removal attempt failed: {e}")

            # small wait before retry
            time.sleep(0.8)

        logs.append("Consent popup present but no known button found to dismiss (after retries)")
        return False

    except Exception as e:
        logs.append(f"dismiss_consent_popup unexpected error: {e}")
        return False


# ---------- selenium worker (modified only for consent + robust clicks) ----------
def selenium_boost_worker(email: str, password: str, num_buttons: int, headless: bool,
                          wait_time: int = DEFAULT_WAIT_TIME) -> BoostResponse:
    logs: List[str] = []
    clicked_addresses: List[Optional[str]] = []
    screenshot_b64 = None
    driver = None
    try:
        logs.append("Starting Selenium worker")

        # do network pre-check unless skipped
        test_url = "https://www.affordablehousing.com/"
        try:
            network_precheck(test_url, timeout=6, logs=logs)
        except Exception as e:
            logs.append(f"Pre-check failed: {e}")
            # re-raise to return a helpful response (unless SKIP_NETWORK_CHECK true)
            if os.environ.get("SKIP_NETWORK_CHECK", "").lower() not in ("1", "true", "yes"):
                raise Exception(
                    f"Network pre-check failed for {test_url}. Either outbound network is blocked or the site resets connections. Full error: {e}"
                )
            # else continue (skip pre-check)

        chrome_bin = find_chrome_binary()
        chromedriver_bin = find_chromedriver_binary()
        logs.append(f"Detected chrome binary: {chrome_bin or '<none>'}")
        logs.append(f"Detected chromedriver binary: {chromedriver_bin or '<none>'}")

        if not chromedriver_bin:
            raise Exception("Chromedriver binary not found. Set CHROMEDRIVER_PATH or install chromedriver in the image.")

        # build Chrome options
        options = webdriver.ChromeOptions()
        if headless:
            options.add_argument("--headless=new")
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--lang=en-US")
        options.add_argument("--disable-extensions")
        options.add_argument("--disable-background-networking")
        options.add_argument("--disable-sync")
        options.add_argument("--metrics-recording-only")
        options.add_argument("--disable-default-apps")
        options.add_argument("--ignore-certificate-errors")
        options.add_argument("--remote-debugging-port=0")
        options.add_argument("--disable-features=VizDisplayCompositor")
        options.add_argument("user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36")

        options.add_experimental_option("excludeSwitches", ["enable-automation"])
        options.add_experimental_option("useAutomationExtension", False)

        if chrome_bin:
            options.binary_location = chrome_bin

        # if proxy env set, pass it to Chrome
        proxy_env = os.environ.get("HTTPS_PROXY") or os.environ.get("HTTP_PROXY")
        if proxy_env:
            logs.append(f"Setting Chrome proxy: {proxy_env}")
            options.add_argument(f"--proxy-server={proxy_env}")

        # instantiate driver
        service = ChromeService(executable_path=chromedriver_bin)
        driver = webdriver.Chrome(service=service, options=options)
        try:
            # small stealth: override navigator.webdriver
            driver.execute_cdp_cmd(
                "Page.addScriptToEvaluateOnNewDocument",
                {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"},
            )
        except Exception:
            pass

        wait = WebDriverWait(driver, wait_time or DEFAULT_WAIT_TIME)

        # robust GET with retries/backoff
        tries = 3
        for attempt in range(1, tries + 1):
            try:
                driver.get("https://www.affordablehousing.com/")
                logs.append("Opened affordablehousing.com (via driver.get)")
                # Attempt to dismiss cookie/consent overlay right away
                try:
                    dismissed = dismiss_consent_popup(driver, logs, max_attempts=3)
                    logs.append(f"dismiss_consent_popup result: {dismissed}")
                except Exception as e:
                    logs.append(f"Error while attempting to dismiss consent popup: {e}")
                break
            except Exception as e:
                logs.append(f"driver.get attempt {attempt} failed: {e}")
                if attempt == tries:
                    raise
                time.sleep(1.5 * attempt)

        wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "li.ah--signin--link"))).click()
        logs.append("Clicked homepage Sign In")

        email_input = wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, "input#ah_user")))
        email_input.clear()
        email_input.send_keys(email)
        logs.append("Entered email")

        wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, "button#signin-button"))).click()
        logs.append("Clicked first Sign In button")

        password_input = wait.until(EC.visibility_of_element_located((By.CSS_SELECTOR, "input#ah_pass")))
        password_input.clear()
        password_input.send_keys(password)
        logs.append("Entered password")

        # Robust final sign-in click with retries and overlay-removal fallback
        signin_selector = (By.CSS_SELECTOR, "button#signin-with-password-button")
        signin_button = wait.until(EC.element_to_be_clickable(signin_selector))

        clicked = False
        for attempt in range(1, 4):
            try:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", signin_button)
                time.sleep(0.4)
                signin_button.click()
                clicked = True
                logs.append("Clicked final Sign In button (normal click)")
                break
            except Exception as e:
                logs.append(f"Sign-in normal click attempt {attempt} failed: {e}")
                try:
                    driver.execute_script("arguments[0].click();", signin_button)
                    clicked = True
                    logs.append("Clicked final Sign In button (JS fallback)")
                    break
                except Exception as je:
                    logs.append(f"Sign-in JS click attempt {attempt} failed: {je}")
                    # Try to remove likely overlay nodes and retry
                    try:
                        driver.execute_script("""
                            document.querySelectorAll('[class*="consent"], [class*="overlay"], [class*="cookie"], [role="dialog"]').forEach(e=>{ try{ e.style.display='none'; e.remove(); } catch(e){}});
                        """)
                        logs.append("Tried removing overlay nodes after click interception")
                    except Exception as re:
                        logs.append(f"Failed overlay cleanup: {re}")
                    time.sleep(0.6)

        if not clicked:
            raise Exception("Could not click final sign-in button after multiple attempts")
        logs.append("Clicked final Sign In button (completed)")

        wait.until(EC.url_contains("dashboard"))
        logs.append("Login confirmed (dashboard)")

        listing_url = "https://www.affordablehousing.com/v4/pages/Listing/Listing.aspx"
        driver.get(listing_url)
        logs.append(f"Navigated to {listing_url}")

        loops = 0
        last_height = driver.execute_script("return document.body.scrollHeight")
        while loops < DEFAULT_MAX_SCROLL_LOOPS:
            driver.execute_script("window.scrollBy(0, window.innerHeight);")
            time.sleep(DEFAULT_SCROLL_PAUSE)
            loops += 1
            new_height = driver.execute_script("return document.body.scrollHeight")
            if new_height == last_height:
                time.sleep(0.5)
                break
            last_height = new_height
        logs.append(f"Finished incremental scrolling ({loops} loops)")

        start = time.time()
        found_count = 0
        found_context = None
        while time.time() - start < DEFAULT_JS_POLL_TIMEOUT:
            c_buttons = int(driver.execute_script(
                "return document.querySelectorAll('button.usage-boost-button, button.cmn--btn.usage-boost-button').length || 0;"))
            logs.append(f"[JS POLL] buttons={c_buttons}")
            if c_buttons > 0:
                found_context = ("main", None)
                found_count = c_buttons
                break
            time.sleep(DEFAULT_JS_POLL_INTERVAL)

        logs.append(f"[JS POLL RESULT] found_context={found_context} found_count={found_count}")
        if not found_context:
            try:
                screenshot_b64 = driver.get_screenshot_as_base64()
                logs.append("No cards/buttons found - saved screenshot")
            except Exception:
                logs.append("Screenshot failed")
            raise Exception("No listing cards/buttons found after JS polling")

        buttons = driver.find_elements(By.CSS_SELECTOR, "button.usage-boost-button, button.cmn--btn.usage-boost-button")
        logs.append(f"Collected {len(buttons)} button elements")

        boostable = []
        for idx, btn in enumerate(buttons, start=1):
            try:
                btn_text = get_element_text_via_js(driver, btn).lower()
                norm = " ".join(btn_text.split())
                classes = (btn.get_attribute("class") or "").lower()
                in_progress = ("usage-boost-inprogress" in classes) or ("progress" in norm) or ("inprogress" in norm)
                if ("boost" in norm) and not in_progress:
                    address = find_address_for_button(driver, btn)
                    boostable.append((address, btn, norm))
                    logs.append(f"[FOUND] btn#{idx} text='{norm}' addr='{address or '<none>'}'")
                else:
                    logs.append(f"[SKIP] btn#{idx} text='{norm[:60]}' in_progress={in_progress}")
            except Exception as e:
                logs.append(f"[WARN] error inspecting btn#{idx}: {e}")

        logs.append(f"Total boostable detected: {len(boostable)}")
        if not boostable:
            try:
                screenshot_b64 = driver.get_screenshot_as_base64()
                logs.append("No boostable buttons after filtering - saved screenshot")
            except Exception:
                logs.append("screenshot failed")
            raise Exception("No boostable buttons found to click")

        to_click = min(num_buttons, len(boostable))
        clicked = 0
        for i in range(to_click):
            address, btn, text = boostable[i]
            try:
                driver.execute_script("arguments[0].scrollIntoView({block:'center'});", btn)
                time.sleep(0.6)
                driver.execute_script("arguments[0].click();", btn)
                clicked += 1
                clicked_addresses.append(address)
                logs.append(f"Clicked boost for: {address or '<address not found>'}")
                time.sleep(1.2)
            except Exception as ce:
                logs.append(f"Error clicking boost #{i+1}: {ce}")

        return BoostResponse(
            success=True,
            clicked_count=clicked,
            clicked_addresses=clicked_addresses,
            debug_logs=logs,
            error=None,
            screenshot_base64=screenshot_b64
        )

    except Exception as exc:
        tb = traceback.format_exc()
        logs.append(f"Unhandled exception: {str(exc)}")
        logs.append(tb)
        try:
            if driver:
                screenshot_b64 = driver.get_screenshot_as_base64()
                logs.append("Captured error screenshot (base64)")
                # Also save page source for debugging
                try:
                    page_html = driver.page_source
                    path = "/tmp/affordablehousing_page.html"
                    with open(path, "w", encoding="utf-8") as f:
                        f.write(page_html)
                    logs.append(f"Saved page source to {path}")
                except Exception as e:
                    logs.append(f"Failed to save page source: {e}")
        except Exception:
            pass

        return BoostResponse(
            success=False,
            clicked_count=0,
            clicked_addresses=[],
            debug_logs=logs,
            error=str(exc),
            screenshot_base64=screenshot_b64
        )

    finally:
        try:
            if driver:
                driver.quit()
                logs.append("Driver.quit() called")
        except Exception:
            pass
