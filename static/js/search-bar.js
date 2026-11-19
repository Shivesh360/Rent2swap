/**
 * Rent2Swap — Marketplace Search, Filter Sync & Live Results Controller
 * Handles clear-button state, subtle animated placeholder hints,
 * bi-directional sync across top search bar, sidebar/drawer filters, mobile sort,
 * removable active filter tags, loading skeletons, and smooth AJAX filtering.
 */
(function () {
  var DEFAULT_HINTS = [
    "Search gear (e.g. Drafter, Sony A7, Cycle)...",
    "Try: MacBook Pro, iPad, or Graphing Calculator...",
    "Try: Engineering textbooks or acoustic guitar...",
    "Try: Campus cycle, tripod, or lab equipment..."
  ];

  var reducedMotion =
    window.matchMedia &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function initSearchInputs() {
    var wraps = document.querySelectorAll(".r2x-search, .r2x-search-mini");
    wraps.forEach(function (wrap) {
      var input = wrap.querySelector("input[name='q']");
      var clearBtn = wrap.querySelector(".r2x-search-clear");
      if (!input) return;

      function syncClearState() {
        if (!clearBtn) return;
        if (input.value && input.value.trim().length > 0) {
          clearBtn.classList.add("is-visible");
        } else {
          clearBtn.classList.remove("is-visible");
        }
      }

      input.addEventListener("input", syncClearState);
      syncClearState();

      if (clearBtn) {
        clearBtn.addEventListener("click", function () {
          input.value = "";
          syncClearState();
          input.dispatchEvent(new Event("input", { bubbles: true }));
          input.focus();
        });
      }

      if (!reducedMotion && wrap.classList.contains("r2x-search")) {
        var customHints = null;
        try {
          var attr = input.getAttribute("data-hints");
          if (attr) customHints = JSON.parse(attr);
        } catch (e) {
          customHints = null;
        }
        var hints = Array.isArray(customHints) && customHints.length ? customHints : DEFAULT_HINTS;
        var hintIndex = 0;

        setInterval(function () {
          if (document.hidden) return;
          if (document.activeElement === input || (input.value && input.value.length > 0)) return;
          hintIndex = (hintIndex + 1) % hints.length;
          input.classList.add("is-hint-fade");
          setTimeout(function () {
            if (document.activeElement !== input && !input.value) {
              input.setAttribute("placeholder", hints[hintIndex]);
            }
            input.classList.remove("is-hint-fade");
          }, 220);
        }, 3600);
      }
    });
  }

  function initMarketplaceController() {
    var mpPage = document.getElementById("marketplace-page");
    var filterForm = document.getElementById("filter-form");
    var searchForm = document.getElementById("marketplace-search-form");
    var filterCard = document.getElementById("filter-sidebar");
    if (!mpPage || !filterForm) return;

    // Trigger staggered reveal
    if (filterCard) {
      if (window.requestAnimationFrame) {
        window.requestAnimationFrame(function () {
          filterCard.classList.add("in");
        });
      } else {
        filterCard.classList.add("in");
      }
    }

    var topInput = document.getElementById("marketplace-search-input");
    var sideInput = document.getElementById("filter-q");
    var topCat = document.getElementById("marketplace-category-select");
    var sideCat = document.getElementById("filter-category");
    var topType = document.getElementById("marketplace-type-select");
    var sideType = document.getElementById("filter-type");
    var sideCond = document.getElementById("filter-condition");
    var sideMin = document.getElementById("filter-min-price");
    var sideMax = document.getElementById("filter-max-price");
    var sideSort = document.getElementById("filter-sort");
    var mobileSort = document.getElementById("mobile-sort-select");

    var searchCondHidden = document.getElementById("search-condition-hidden");
    var searchCityHidden = document.getElementById("search-city-hidden");
    var searchMinHidden = document.getElementById("search-min-price-hidden");
    var searchMaxHidden = document.getElementById("search-max-price-hidden");
    var searchSortHidden = document.getElementById("search-sort-hidden");

    var seg = filterCard ? filterCard.querySelector(".r2x-seg") : null;
    var glider = filterCard ? filterCard.querySelector(".r2x-seg-glider") : null;
    var segBtns = seg ? Array.prototype.slice.call(seg.querySelectorAll(".r2x-seg-btn")) : [];
    var sortBtns = filterCard ? Array.prototype.slice.call(filterCard.querySelectorAll(".r2x-sort-btn")) : [];

    function positionGlider(val) {
      if (!seg || !glider) return;
      var activeIdx = 0;
      segBtns.forEach(function (btn, idx) {
        var isMatch = btn.getAttribute("data-value") === val;
        btn.classList.toggle("is-active", isMatch);
        btn.setAttribute("aria-pressed", isMatch ? "true" : "false");
        if (isMatch) activeIdx = idx;
      });
      seg.setAttribute("data-active", val || "All");
      glider.style.transform = "translateX(" + activeIdx * 100 + "%)";
    }

    function updateSortUI(sortVal) {
      if (sideSort) sideSort.value = sortVal;
      if (searchSortHidden) searchSortHidden.value = sortVal;
      if (mobileSort && mobileSort.value !== sortVal) mobileSort.value = sortVal;
      sortBtns.forEach(function (btn) {
        var active = btn.getAttribute("data-sort") === sortVal;
        btn.classList.toggle("is-active", active);
        btn.setAttribute("aria-pressed", active ? "true" : "false");
      });
    }

    function syncHiddenSearchFields() {
      if (sideInput && topInput) sideInput.value = topInput.value;
      if (searchCondHidden && sideCond) searchCondHidden.value = sideCond.value || "All";
      if (searchMinHidden && sideMin) searchMinHidden.value = sideMin.value || "";
      if (searchMaxHidden && sideMax) searchMaxHidden.value = sideMax.value || "";
      if (searchSortHidden && sideSort) searchSortHidden.value = sideSort.value || "newest";
    }

    function closeMobileDrawer() {
      var drawerEl = document.getElementById("marketplaceFiltersDrawer");
      if (!drawerEl || typeof bootstrap === "undefined" || !bootstrap.Offcanvas) return;
      var inst = bootstrap.Offcanvas.getInstance(drawerEl);
      if (inst) inst.hide();
    }

    function buildQueryParams() {
      syncHiddenSearchFields();
      var params = new URLSearchParams();
      var qVal = sideInput ? sideInput.value.trim() : "";
      var catVal = sideCat ? sideCat.value : "All";
      var typeVal = sideType ? sideType.value : "All";
      var condVal = sideCond ? sideCond.value : "All";
      var cityVal = searchCityHidden ? searchCityHidden.value : "All";
      var minVal = sideMin ? sideMin.value.trim() : "";
      var maxVal = sideMax ? sideMax.value.trim() : "";
      var sortVal = sideSort ? sideSort.value : "newest";

      if (qVal) params.set("q", qVal);
      if (catVal && catVal !== "All") params.set("category", catVal);
      if (typeVal && typeVal !== "All") params.set("type", typeVal);
      if (condVal && condVal !== "All") params.set("condition", condVal);
      if (cityVal && cityVal !== "All") params.set("city", cityVal);
      if (minVal) params.set("min_price", minVal);
      if (maxVal) params.set("max_price", maxVal);
      if (sortVal && sortVal !== "newest") params.set("sort", sortVal);
      return params;
    }

    function syncControlsFromUrl(urlString) {
      var u = new URL(urlString, window.location.origin);
      var sp = u.searchParams;

      var q = sp.get("q") || "";
      var cat = sp.get("category") || "All";
      var type = sp.get("type") || "All";
      var cond = sp.get("condition") || "All";
      var city = sp.get("city") || "All";
      var minP = sp.get("min_price") || "";
      var maxP = sp.get("max_price") || "";
      var sort = sp.get("sort") || "newest";

      if (topInput) {
        topInput.value = q;
        topInput.dispatchEvent(new Event("input", { bubbles: false }));
      }
      if (sideInput) sideInput.value = q;
      if (topCat) topCat.value = cat;
      if (sideCat) sideCat.value = cat;
      if (topType) topType.value = type;
      if (sideType) sideType.value = type;
      if (sideCond) sideCond.value = cond;
      if (searchCityHidden) searchCityHidden.value = city;
      if (sideMin) sideMin.value = minP;
      if (sideMax) sideMax.value = maxP;

      positionGlider(type);
      updateSortUI(sort);
      syncHiddenSearchFields();
    }

    var activeFetchController = null;

    function loadMarketplaceUrl(targetUrl, pushState) {
      var resultsRegion = document.getElementById("marketplace-results-region");
      var skeletonGrid = document.getElementById("marketplace-skeleton-grid");
      var activeStrip = document.getElementById("marketplace-active-strip");
      var countBadge = document.getElementById("marketplace-count-badge");
      var mobileFilterBtn = document.getElementById("mobile-filters-trigger");

      if (!resultsRegion || !window.fetch || !window.DOMParser) {
        window.location.href = targetUrl;
        return;
      }

      if (activeFetchController && activeFetchController.abort) {
        activeFetchController.abort();
      }
      activeFetchController = window.AbortController ? new AbortController() : null;

      resultsRegion.classList.add("is-loading", "d-none");
      if (skeletonGrid) skeletonGrid.classList.remove("d-none");

      var fetchOpts = {
        headers: { "X-Requested-With": "fetch" }
      };
      if (activeFetchController) fetchOpts.signal = activeFetchController.signal;

      fetch(targetUrl, fetchOpts)
        .then(function (res) {
          if (!res.ok) throw new Error("HTTP " + res.status);
          return res.text();
        })
        .then(function (html) {
          var doc = new DOMParser().parseFromString(html, "text/html");
          var nextResults = doc.getElementById("marketplace-results-region");
          if (!nextResults) {
            window.location.href = targetUrl;
            return;
          }
          resultsRegion.innerHTML = nextResults.innerHTML;

          var nextStrip = doc.getElementById("marketplace-active-strip");
          if (activeStrip && nextStrip) activeStrip.innerHTML = nextStrip.innerHTML;

          var nextCount = doc.getElementById("marketplace-count-badge");
          if (countBadge && nextCount) countBadge.innerHTML = nextCount.innerHTML;

          var nextMobileBtn = doc.getElementById("mobile-filters-trigger");
          if (mobileFilterBtn && nextMobileBtn) mobileFilterBtn.innerHTML = nextMobileBtn.innerHTML;

          if (pushState && window.history && window.history.pushState) {
            window.history.pushState({ r2mUrl: targetUrl }, "", targetUrl);
          }

          if (skeletonGrid) skeletonGrid.classList.add("d-none");
          resultsRegion.classList.remove("d-none", "is-loading");
        })
        .catch(function (err) {
          if (err && err.name === "AbortError") return;
          if (skeletonGrid) skeletonGrid.classList.add("d-none");
          resultsRegion.classList.remove("d-none", "is-loading");
          window.location.href = targetUrl;
        });
    }

    function applyCurrentFilters(pushState) {
      var params = buildQueryParams();
      var qs = params.toString();
      var baseUrl = filterForm.getAttribute("action") || "/marketplace";
      var targetUrl = qs ? (baseUrl + "?" + qs) : baseUrl;
      closeMobileDrawer();
      loadMarketplaceUrl(targetUrl, pushState !== false);
    }

    // Initial glider state
    if (sideType) positionGlider(sideType.value || "All");

    // Input bi-directional sync
    if (topInput && sideInput) {
      topInput.addEventListener("input", function () {
        if (sideInput.value !== topInput.value) sideInput.value = topInput.value;
      });
    }

    if (topCat && sideCat) {
      topCat.addEventListener("change", function () {
        sideCat.value = topCat.value;
        applyCurrentFilters(true);
      });
      sideCat.addEventListener("change", function () {
        topCat.value = sideCat.value;
        applyCurrentFilters(true);
      });
    }

    if (topType && sideType) {
      topType.addEventListener("change", function () {
        sideType.value = topType.value;
        positionGlider(sideType.value || "All");
        applyCurrentFilters(true);
      });
    }

    if (sideCond) {
      sideCond.addEventListener("change", function () {
        syncHiddenSearchFields();
        applyCurrentFilters(true);
      });
    }

    segBtns.forEach(function (btn) {
      btn.addEventListener("click", function () {
        var nextVal = btn.getAttribute("data-value") || "All";
        if (sideType) sideType.value = nextVal;
        positionGlider(nextVal);
        if (topType && topType.value !== nextVal) topType.value = nextVal;
        applyCurrentFilters(true);
      });
    });

    sortBtns.forEach(function (btn) {
      btn.addEventListener("click", function () {
        var nextSort = btn.getAttribute("data-sort") || "newest";
        updateSortUI(nextSort);
        applyCurrentFilters(true);
      });
    });

    if (mobileSort) {
      mobileSort.addEventListener("change", function () {
        updateSortUI(mobileSort.value || "newest");
        applyCurrentFilters(true);
      });
    }

    filterForm.addEventListener("submit", function (e) {
      e.preventDefault();
      applyCurrentFilters(true);
    });

    if (searchForm) {
      searchForm.addEventListener("submit", function (e) {
        e.preventDefault();
        applyCurrentFilters(true);
      });
    }

    // Delegated clicks for active filter removal & clear filters
    mpPage.addEventListener("click", function (e) {
      var chip = e.target.closest && e.target.closest(".r2m-active-chip[data-clear-param]");
      if (chip) {
        e.preventDefault();
        var param = chip.getAttribute("data-clear-param");
        if (param === "q") {
          if (topInput) {
            topInput.value = "";
            topInput.dispatchEvent(new Event("input", { bubbles: false }));
          }
          if (sideInput) sideInput.value = "";
        } else if (param === "category") {
          if (topCat) topCat.value = "All";
          if (sideCat) sideCat.value = "All";
        } else if (param === "type") {
          if (topType) topType.value = "All";
          if (sideType) sideType.value = "All";
          positionGlider("All");
        } else if (param === "condition") {
          if (sideCond) sideCond.value = "All";
        } else if (param === "city") {
          if (searchCityHidden) searchCityHidden.value = "All";
        } else if (param === "price") {
          if (sideMin) sideMin.value = "";
          if (sideMax) sideMax.value = "";
        } else if (param === "sort") {
          updateSortUI("newest");
        }
        applyCurrentFilters(true);
        return;
      }

      var clearLink = e.target.closest && e.target.closest("#btn-reset-filters, #clear-all-filters-btn, #empty-clear-filters-btn");
      if (clearLink) {
        e.preventDefault();
        var baseUrl = clearLink.getAttribute("href") || "/marketplace";
        syncControlsFromUrl(baseUrl);
        closeMobileDrawer();
        loadMarketplaceUrl(baseUrl, true);
      }
    });

    window.addEventListener("popstate", function () {
      var currUrl = window.location.pathname + window.location.search;
      syncControlsFromUrl(currUrl);
      loadMarketplaceUrl(currUrl, false);
    });
  }

  function boot() {
    initSearchInputs();
    initMarketplaceController();
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
