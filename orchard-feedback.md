# Orchard Feedback: Crash Triage on ZMScrollGalleryView Negative Size

Date: 2026-06-30
Repo: ios-client
Crash log: `Zoom_7.1.0_2026-06-30_05_55_46_Harshith’s_iphone.crashlog`

## Scenario

The crash was an iPhone main-thread abort:

`UICollectionViewFlowLayout` received a negative item size `{-1, -1}` from `ZMScrollGalleryView` during a meeting chat presentation triggered from the participant list context menu.

The root cause investigation traced the invalid size to iPhone gallery sizing code in `ZMScrollGalleryViewModel_iPhone`, where `cellSizeWithContainerBounds:` could return a non-positive size during narrow/transitional layout states. The fix clamps returned dimensions to at least `1.f`.

## What Worked Well

- `orchard_lookup_crash_thread` confirmed the index status was `fresh`, which was useful before trusting any symbol-level results.
- `orchard_search` successfully found the exact Objective-C methods:
  - `ZMScrollGalleryViewModel_iPhone::cellSizeWithContainerBounds:`
  - `ZMScrollGalleryViewModel_iPhone::cellWidthWithContainerBounds:`
- `orchard_find_references` helped confirm the sizing methods mainly depend on viewport, safe area, status bar, and toolbar state.
- `orchard_find_callers` returning no static callers was useful signal in context: this path is reached through UIKit delegate dynamic dispatch, not ordinary direct calls.

## What Was Confusing

- `orchard_lookup_crash_thread` selected a misleading first indexed business symbol: a `prepareLayout` implementation from unrelated collection view layout code.
- The actual application-specific crash reason explicitly named `ZMScrollGalleryView` as the delegate returning `{-1, -1}`, but Orchard did not appear to use that exception reason to prioritize symbol lookup.
- UIKit private frames caused near matches against local classes or categories with framework-like names, which made the initial summary noisier than expected.

## Suggested Improvements

- Parse `Application Specific Information` and prioritize symbols mentioned there, especially delegate object classes and selector names.
- For crashes caused by UIKit delegate callbacks, infer likely app selectors from exception text, for example:
  - `collectionView:layout:sizeForItemAtIndexPath:`
  - delegate class `ZMScrollGalleryView`
- When a frame is a UIKit private method but the exception reason names an app delegate/data source, rank the delegate selector above framework near matches.
- Surface a warning when the first indexed business symbol is selected by owner/symbol fallback rather than by a concrete app frame or source-level call edge.
- Include a "dynamic dispatch likely" note when `find_callers` returns no static callers for ObjC delegate methods.

## Overall

Orchard was helpful as a compiler-indexed verification tool for symbol existence, index freshness, and local references. It was less reliable as the primary crash root-cause locator for this UIKit delegate exception because the key evidence lived in the exception reason rather than the stack frames.
