/**
 * SAMPLE DATA — Zaika demo menu.
 *
 * Replace with GET /guest/menu (api-plan.md § 3) in Stage 3. Prices
 * below are demo display values only and are NEVER billing authority:
 * the backend recomputes every price from its own menu at order time
 * (api-plan.md § 4.1) and the frontend never sends prices.
 *
 * Money is integer paise everywhere. Images are Unsplash photo URLs;
 * FoodImage falls back to a styled placeholder if one fails to load.
 */
import type { MenuCategory, MenuItem, RestaurantInfo } from "@/shared/api/types";

export const ZAIKA_RESTAURANT: RestaurantInfo = {
  id: "zaika-demo",
  name: "Zaika",
  tagline: "North Indian kitchen · slow-cooked, generously spiced",
  cuisine: "North Indian · Tandoor · Biryani",
  address: "12, MG Road, Bengaluru",
  openingHours: "11:00 AM – 11:00 PM · All days",
  phone: "+91 98765 43210",
  rating: 4.6,
};

export const ZAIKA_CATEGORIES: MenuCategory[] = [
  { id: "starters", name: "Starters", description: "Small plates to begin", sortOrder: 1, isActive: true },
  { id: "main-course", name: "Main Course", description: "Curries from the kadhai", sortOrder: 2, isActive: true },
  { id: "breads", name: "Breads", description: "From the tandoor", sortOrder: 3, isActive: true },
  { id: "rice-biryani", name: "Rice & Biryani", description: "Dum-cooked, served with raita", sortOrder: 4, isActive: true },
  { id: "beverages", name: "Beverages", description: "Coolers, lassi & masala chai", sortOrder: 5, isActive: true },
  { id: "desserts", name: "Desserts", description: "Something sweet to finish", sortOrder: 6, isActive: true },
];

const img = (id: string) =>
  `https://images.unsplash.com/${id}?w=800&q=80&auto=format&fit=crop`;

export const ZAIKA_MENU_ITEMS: MenuItem[] = [
  // ---- Starters -----------------------------------------------------
  {
    id: "paneer-tikka",
    categoryId: "starters",
    name: "Paneer Tikka Angara",
    description:
      "Char-grilled cottage cheese cubes marinated overnight in hung curd, Kashmiri chilli and ajwain, served with mint chutney.",
    pricePaise: 24900,
    isVeg: true,
    isAvailable: true,
    isPopular: true,
    imageUrl: img("photo-1631452180519-c014fe946bc7"),
    station: "Tandoor",
    variantGroupLabel: "Spice level",
    variants: [
      { id: "spice-mild", name: "Mild", priceDeltaPaise: 0, isAvailable: true },
      { id: "spice-medium", name: "Medium", priceDeltaPaise: 0, isAvailable: true },
      { id: "spice-hot", name: "Angara hot", priceDeltaPaise: 0, isAvailable: true },
    ],
    addOns: [
      { id: "extra-chutney", name: "Extra mint chutney", pricePaise: 2000, isAvailable: true },
      { id: "extra-onion", name: "Extra onion rings", pricePaise: 1500, isAvailable: true },
    ],
  },
  {
    id: "samosa-chaat",
    categoryId: "starters",
    name: "Samosa Chaat",
    description:
      "Crushed samosa layered with chole, whisked curd, tamarind and green chutneys, sev and pomegranate.",
    pricePaise: 14900,
    isVeg: true,
    isAvailable: true,
    isPopular: true,
    imageUrl: img("photo-1601050690597-df0568f70950"),
    station: "Main Kitchen",
    addOns: [
      { id: "extra-curd", name: "Extra curd", pricePaise: 2000, isAvailable: true },
      { id: "extra-sev", name: "Extra sev", pricePaise: 1500, isAvailable: true },
    ],
  },
  {
    id: "chicken-65",
    categoryId: "starters",
    name: "Chicken 65",
    description:
      "Fiery curry-leaf tossed fried chicken with green chillies and a squeeze of lemon. A South Indian classic.",
    pricePaise: 26900,
    isVeg: false,
    isAvailable: true,
    isPopular: false,
    imageUrl: img("photo-1599487488170-d11ec9c172f0"),
    station: "Main Kitchen",
    variantGroupLabel: "Spice level",
    variants: [
      { id: "spice-mild", name: "Mild", priceDeltaPaise: 0, isAvailable: true },
      { id: "spice-medium", name: "Medium", priceDeltaPaise: 0, isAvailable: true },
      { id: "spice-hot", name: "Extra hot", priceDeltaPaise: 0, isAvailable: true },
    ],
  },
  {
    id: "hara-bhara-kebab",
    categoryId: "starters",
    name: "Hara Bhara Kebab",
    description:
      "Spinach, green peas and potato patties, shallow-fried till crisp, with a cashew centre.",
    pricePaise: 19900,
    isVeg: true,
    isAvailable: false,
    isPopular: false,
    imageUrl: img("photo-1546833999-b9f581a1996d"),
    station: "Main Kitchen",
  },

  // ---- Main Course --------------------------------------------------
  {
    id: "butter-chicken",
    categoryId: "main-course",
    name: "Old Delhi Butter Chicken",
    description:
      "Tandoori chicken folded into a silky tomato-butter gravy with kasuri methi. Zaika's most-ordered dish.",
    pricePaise: 34900,
    isVeg: false,
    isAvailable: true,
    isPopular: true,
    imageUrl: img("photo-1585937421612-70a008356fbe"),
    station: "Main Kitchen",
    variantGroupLabel: "Portion",
    variants: [
      { id: "portion-half", name: "Half", priceDeltaPaise: -10000, isAvailable: true },
      { id: "portion-full", name: "Full", priceDeltaPaise: 0, isAvailable: true },
    ],
    addOns: [
      { id: "extra-gravy", name: "Extra gravy", pricePaise: 5000, isAvailable: true },
      { id: "extra-butter", name: "Extra white butter", pricePaise: 2000, isAvailable: true },
      { id: "extra-cream", name: "Extra cream", pricePaise: 3000, isAvailable: true },
    ],
  },
  {
    id: "paneer-butter-masala",
    categoryId: "main-course",
    name: "Paneer Butter Masala",
    description:
      "Soft paneer in a rich tomato-cashew gravy, finished with butter and cream. Mildly sweet, gently spiced.",
    pricePaise: 28900,
    isVeg: true,
    isAvailable: true,
    isPopular: true,
    imageUrl: img("photo-1565557623262-b51c2513a641"),
    station: "Main Kitchen",
    variantGroupLabel: "Spice level",
    variants: [
      { id: "spice-mild", name: "Mild", priceDeltaPaise: 0, isAvailable: true },
      { id: "spice-medium", name: "Medium", priceDeltaPaise: 0, isAvailable: true },
      { id: "spice-hot", name: "Spicy", priceDeltaPaise: 0, isAvailable: true },
    ],
    addOns: [
      { id: "extra-gravy", name: "Extra gravy", pricePaise: 5000, isAvailable: true },
      { id: "extra-paneer", name: "Extra paneer", pricePaise: 6000, isAvailable: true },
    ],
  },
  {
    id: "dal-makhani",
    categoryId: "main-course",
    name: "Dal Makhani",
    description:
      "Black urad simmered overnight on slow embers, with tomato, butter and cream. The way it should be.",
    pricePaise: 22900,
    isVeg: true,
    isAvailable: true,
    isPopular: true,
    imageUrl: img("photo-1546833999-b9f581a1996d"),
    station: "Main Kitchen",
    variantGroupLabel: "Portion",
    variants: [
      { id: "portion-half", name: "Half", priceDeltaPaise: -7000, isAvailable: true },
      { id: "portion-full", name: "Full", priceDeltaPaise: 0, isAvailable: true },
    ],
    addOns: [
      { id: "extra-butter", name: "Extra white butter", pricePaise: 2000, isAvailable: true },
      { id: "extra-cream", name: "Extra cream", pricePaise: 3000, isAvailable: true },
    ],
  },
  {
    id: "mutton-rogan-josh",
    categoryId: "main-course",
    name: "Mutton Rogan Josh",
    description:
      "Slow-braised mutton in a Kashmiri chilli and fennel gravy, cooked till the meat falls off the bone.",
    pricePaise: 39900,
    isVeg: false,
    isAvailable: true,
    isPopular: false,
    imageUrl: img("photo-1596797038530-2c107229654b"),
    station: "Main Kitchen",
    addOns: [
      { id: "extra-gravy", name: "Extra gravy", pricePaise: 5000, isAvailable: true },
    ],
  },

  // ---- Breads -------------------------------------------------------
  {
    id: "butter-naan",
    categoryId: "breads",
    name: "Butter Naan",
    description: "Soft leavened bread from the tandoor, brushed with white butter.",
    pricePaise: 4900,
    isVeg: true,
    isAvailable: true,
    isPopular: false,
    imageUrl: img("photo-1606755962773-d324e0a13086"),
    station: "Tandoor",
    addOns: [
      { id: "extra-butter", name: "Extra butter", pricePaise: 2000, isAvailable: true },
    ],
  },
  {
    id: "garlic-naan",
    categoryId: "breads",
    name: "Garlic Naan",
    description: "Naan studded with roasted garlic and coriander, brushed with butter.",
    pricePaise: 6900,
    isVeg: true,
    isAvailable: true,
    isPopular: true,
    imageUrl: img("photo-1606755962773-d324e0a13086"),
    station: "Tandoor",
    addOns: [
      { id: "extra-cheese", name: "Extra cheese", pricePaise: 4000, isAvailable: true },
      { id: "extra-butter", name: "Extra butter", pricePaise: 2000, isAvailable: true },
    ],
  },
  {
    id: "amritsari-kulcha",
    categoryId: "breads",
    name: "Amritsari Kulcha",
    description:
      "Stuffed with spiced potatoes, baked crisp in the tandoor, served with white butter and pickle.",
    pricePaise: 9900,
    isVeg: true,
    isAvailable: true,
    isPopular: false,
    imageUrl: img("photo-1606755962773-d324e0a13086"),
    station: "Tandoor",
    variantGroupLabel: "Stuffing",
    variants: [
      { id: "stuff-aloo", name: "Aloo", priceDeltaPaise: 0, isAvailable: true },
      { id: "stuff-paneer", name: "Paneer", priceDeltaPaise: 3000, isAvailable: true },
      { id: "stuff-mix", name: "Mixed veg", priceDeltaPaise: 2000, isAvailable: true },
    ],
  },
  {
    id: "tandoori-roti",
    categoryId: "breads",
    name: "Tandoori Roti",
    description: "Whole-wheat roti, blistered in the tandoor. Light and everyday.",
    pricePaise: 3500,
    isVeg: true,
    isAvailable: true,
    isPopular: false,
    imageUrl: img("photo-1606755962773-d324e0a13086"),
    station: "Tandoor",
  },

  // ---- Rice & Biryani -----------------------------------------------
  {
    id: "hyderabadi-chicken-biryani",
    categoryId: "rice-biryani",
    name: "Hyderabadi Chicken Biryani",
    description:
      "Dum-cooked basmati layered with masala chicken, saffron and fried onions. Served with mirchi ka salan and raita.",
    pricePaise: 29900,
    isVeg: false,
    isAvailable: true,
    isPopular: true,
    imageUrl: img("photo-1567188040759-fb8a883dc6d6"),
    station: "Main Kitchen",
    variantGroupLabel: "Portion",
    variants: [
      { id: "portion-half", name: "Half", priceDeltaPaise: -8000, isAvailable: true },
      { id: "portion-full", name: "Full", priceDeltaPaise: 0, isAvailable: true },
    ],
    addOns: [
      { id: "extra-raita", name: "Extra raita", pricePaise: 3000, isAvailable: true },
      { id: "extra-salan", name: "Extra mirchi ka salan", pricePaise: 2000, isAvailable: true },
      { id: "extra-chicken", name: "Extra chicken piece", pricePaise: 7000, isAvailable: true },
    ],
  },
  {
    id: "veg-biryani",
    categoryId: "rice-biryani",
    name: "Subz Dum Biryani",
    description:
      "Seasonal vegetables and paneer layered with saffron rice, sealed and dum-cooked. Served with raita.",
    pricePaise: 23900,
    isVeg: true,
    isAvailable: true,
    isPopular: false,
    imageUrl: img("photo-1589302168068-964664d93dc0"),
    station: "Main Kitchen",
    variantGroupLabel: "Portion",
    variants: [
      { id: "portion-half", name: "Half", priceDeltaPaise: -6000, isAvailable: true },
      { id: "portion-full", name: "Full", priceDeltaPaise: 0, isAvailable: true },
    ],
    addOns: [
      { id: "extra-raita", name: "Extra raita", pricePaise: 3000, isAvailable: true },
    ],
  },
  {
    id: "mutton-biryani",
    categoryId: "rice-biryani",
    name: "Mutton Biryani",
    description:
      "Tender mutton pieces slow-cooked with aged basmati, whole spices and saffron. The house special.",
    pricePaise: 37900,
    isVeg: false,
    isAvailable: false,
    isPopular: true,
    imageUrl: img("photo-1589301760014-d929f3979dbc"),
    station: "Main Kitchen",
    variantGroupLabel: "Portion",
    variants: [
      { id: "portion-half", name: "Half", priceDeltaPaise: -10000, isAvailable: true },
      { id: "portion-full", name: "Full", priceDeltaPaise: 0, isAvailable: true },
    ],
    addOns: [
      { id: "extra-raita", name: "Extra raita", pricePaise: 3000, isAvailable: true },
    ],
  },
  {
    id: "jeera-rice",
    categoryId: "rice-biryani",
    name: "Jeera Rice",
    description: "Basmati tossed with cumin, ghee and coriander. The quiet partner to every curry.",
    pricePaise: 14900,
    isVeg: true,
    isAvailable: true,
    isPopular: false,
    imageUrl: img("photo-1606491956689-2ea866880c84"),
    station: "Main Kitchen",
  },

  // ---- Beverages ----------------------------------------------------
  {
    id: "sweet-lassi",
    categoryId: "beverages",
    name: "Sweet Punjabi Lassi",
    description: "Thick churned curd, sweetened, topped with malai. Served chilled in a tall glass.",
    pricePaise: 9900,
    isVeg: true,
    isAvailable: true,
    isPopular: true,
    imageUrl: img("photo-1551024709-8f23befc6f87"),
    station: "Beverages",
    variantGroupLabel: "Size",
    variants: [
      { id: "size-regular", name: "Regular", priceDeltaPaise: 0, isAvailable: true },
      { id: "size-large", name: "Large", priceDeltaPaise: 4000, isAvailable: true },
    ],
  },
  {
    id: "masala-chai",
    categoryId: "beverages",
    name: "Masala Chai",
    description: "Assam leaves brewed with milk, ginger, cardamom and a whisper of black pepper.",
    pricePaise: 4900,
    isVeg: true,
    isAvailable: true,
    isPopular: false,
    imageUrl: img("photo-1571934811356-5cc061b6821f"),
    station: "Beverages",
    variantGroupLabel: "Size",
    variants: [
      { id: "size-regular", name: "Regular", priceDeltaPaise: 0, isAvailable: true },
      { id: "size-large", name: "Large", priceDeltaPaise: 2000, isAvailable: true },
    ],
  },
  {
    id: "mango-lassi",
    categoryId: "beverages",
    name: "Mango Lassi",
    description: "Alphonso mango pulp folded into thick curd. Seasonal, and worth it.",
    pricePaise: 11900,
    isVeg: true,
    isAvailable: true,
    isPopular: false,
    imageUrl: img("photo-1541544741938-0af808871cc0"),
    station: "Beverages",
    variantGroupLabel: "Size",
    variants: [
      { id: "size-regular", name: "Regular", priceDeltaPaise: 0, isAvailable: true },
      { id: "size-large", name: "Large", priceDeltaPaise: 4000, isAvailable: true },
    ],
  },
  {
    id: "fresh-lime-soda",
    categoryId: "beverages",
    name: "Fresh Lime Soda",
    description: "Hand-squeezed lime with soda, salt or sugar — your call.",
    pricePaise: 7900,
    isVeg: true,
    isAvailable: true,
    isPopular: false,
    imageUrl: img("photo-1437418747212-8d9709afab22"),
    station: "Beverages",
    variantGroupLabel: "Style",
    variants: [
      { id: "style-sweet", name: "Sweet", priceDeltaPaise: 0, isAvailable: true },
      { id: "style-salted", name: "Salted", priceDeltaPaise: 0, isAvailable: true },
      { id: "style-mixed", name: "Sweet & salted", priceDeltaPaise: 0, isAvailable: true },
    ],
  },

  // ---- Desserts -----------------------------------------------------
  {
    id: "gulab-jamun",
    categoryId: "desserts",
    name: "Gulab Jamun (2 pc)",
    description: "Warm khoya dumplings in rose-saffron syrup, with a pistachio dusting.",
    pricePaise: 9900,
    isVeg: true,
    isAvailable: true,
    isPopular: true,
    imageUrl: img("photo-1551024506-0bccd828d307"),
    station: "Dessert",
    addOns: [
      { id: "extra-vanilla", name: "Vanilla ice cream scoop", pricePaise: 5000, isAvailable: true },
    ],
  },
  {
    id: "rasmalai",
    categoryId: "desserts",
    name: "Rasmalai (2 pc)",
    description: "Soft chenna discs soaked in saffron milk, chilled and topped with pistachio.",
    pricePaise: 12900,
    isVeg: true,
    isAvailable: true,
    isPopular: false,
    imageUrl: img("photo-1488477181946-6428a0291777"),
    station: "Dessert",
  },
  {
    id: "kulfi-falooda",
    categoryId: "desserts",
    name: "Kulfi Falooda",
    description: "Dense malai kulfi over rose falooda noodles, basil seeds and rabri.",
    pricePaise: 14900,
    isVeg: true,
    isAvailable: true,
    isPopular: true,
    imageUrl: img("photo-1565958011703-44f9829ba187"),
    station: "Dessert",
    addOns: [
      { id: "extra-kulfi", name: "Extra kulfi slice", pricePaise: 6000, isAvailable: true },
    ],
  },
  {
    id: "chocolate-brownie",
    categoryId: "desserts",
    name: "Chocolate Brownie with Ice Cream",
    description: "Warm dark-chocolate brownie, vanilla ice cream and chocolate sauce.",
    pricePaise: 17900,
    isVeg: true,
    isAvailable: true,
    isPopular: false,
    imageUrl: img("photo-1578985545062-69928b1d9587"),
    station: "Dessert",
  },
];

export const ZAIKA_MENU = {
  restaurant: ZAIKA_RESTAURANT,
  categories: ZAIKA_CATEGORIES,
  items: ZAIKA_MENU_ITEMS,
};

export function findMenuItem(id: string): MenuItem | undefined {
  return ZAIKA_MENU_ITEMS.find((item) => item.id === id);
}
