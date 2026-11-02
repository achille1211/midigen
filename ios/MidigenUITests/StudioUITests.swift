import XCTest

final class StudioUITests: XCTestCase {
    func testGeneratePlaySaveAndRestore() {
        let app = XCUIApplication()
        app.launch()
        let generate = app.buttons["Generate eight bars"]
        XCTAssertTrue(generate.waitForExistence(timeout: 10))
        generate.tap()
        let play = app.buttons["Play"]
        let ready = NSPredicate(format: "enabled == true")
        expectation(for: ready, evaluatedWith: play)
        waitForExpectations(timeout: 60)
        play.tap()
        XCTAssertTrue(app.buttons["Stop"].waitForExistence(timeout: 5))
        app.buttons["Stop"].tap()
        XCTAssertTrue(app.buttons["WAV"].exists)
        let save = app.buttons["Save project"]
        for _ in 0..<6 where !save.isHittable { app.swipeUp() }
        XCTAssertTrue(save.isHittable)
        save.tap()
        app.buttons["Open saved"].tap()
        let restored = NSPredicate(format: "label == %@", "Saved project restored.")
        expectation(for: restored, evaluatedWith: app.staticTexts["studioStatus"])
        waitForExpectations(timeout: 60)
    }
}
