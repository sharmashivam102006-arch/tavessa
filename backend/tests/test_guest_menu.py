"""Guest menu — shape matches the frontend contract (Stage 3)."""
from tests.helpers import headers, make_token, menu_by_name, open_session, seed


def test_menu_shape_matches_frontend_contract(client, db_session):
    seed(db_session)
    token = make_token(db_session, 5)
    open_session(client, token)
    response = client.get("/api/v1/guest/menu", headers=headers(token))
    assert response.status_code == 200
    body = response.json()
    assert len(body["categories"]) == 6
    assert len(body["items"]) == 24
    category = body["categories"][0]
    assert set(category) == {"id", "name", "description", "sortOrder", "isActive"}
    assert category["name"] == "Starters" and category["sortOrder"] == 1
    item = next(i for i in body["items"] if i["name"] == "Old Delhi Butter Chicken")
    assert set(item) >= {
        "id", "categoryId", "name", "description", "pricePaise", "isVeg",
        "isAvailable", "isPopular", "imageUrl", "station", "variantGroupLabel",
        "variants", "addOns",
    }
    assert item["pricePaise"] == 34900
    assert item["isVeg"] is False
    assert item["station"] == "Main Kitchen"
    assert item["variantGroupLabel"] == "Portion"
    half = next(v for v in item["variants"] if v["name"] == "Half")
    assert half["priceDeltaPaise"] == -10000
    assert set(half) == {"id", "name", "priceDeltaPaise", "isAvailable"}
    gravy = next(a for a in item["addOns"] if a["name"] == "Extra gravy")
    assert gravy["pricePaise"] == 5000
    assert set(gravy) == {"id", "name", "pricePaise", "isAvailable"}


def test_unavailable_items_present_but_flagged(client, db_session):
    seed(db_session)
    token = make_token(db_session, 5)
    open_session(client, token)
    _, body = menu_by_name(client, token)
    sold_out = sorted(i["name"] for i in body["items"] if not i["isAvailable"])
    assert sold_out == ["Hara Bhara Kebab", "Mutton Biryani"]


def test_menu_category_filter(client, db_session):
    seed(db_session)
    token = make_token(db_session, 5)
    open_session(client, token)
    _, body = menu_by_name(client, token)
    desserts = next(c for c in body["categories"] if c["name"] == "Desserts")
    response = client.get(
        "/api/v1/guest/menu",
        params={"categoryId": desserts["id"]},
        headers=headers(token),
    )
    assert response.status_code == 200
    items = response.json()["items"]
    assert len(items) == 4
    assert all(i["categoryId"] == desserts["id"] for i in items)
    assert sorted(i["name"] for i in items) == [
        "Chocolate Brownie with Ice Cream",
        "Gulab Jamun (2 pc)",
        "Kulfi Falooda",
        "Rasmalai (2 pc)",
    ]


def test_menu_search(client, db_session):
    seed(db_session)
    token = make_token(db_session, 5)
    open_session(client, token)
    response = client.get(
        "/api/v1/guest/menu", params={"search": "biryani"}, headers=headers(token)
    )
    assert response.status_code == 200
    names = sorted(i["name"] for i in response.json()["items"])
    assert names == [
        "Hyderabadi Chicken Biryani",
        "Mutton Biryani",
        "Subz Dum Biryani",
    ]
    response = client.get(
        "/api/v1/guest/menu", params={"search": "BUTTER"}, headers=headers(token)
    )
    assert response.status_code == 200
    assert any(i["name"] == "Old Delhi Butter Chicken" for i in response.json()["items"])
