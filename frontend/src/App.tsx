import { RouterProvider } from "react-router";

import { Providers } from "@/app/providers";
import { router } from "@/app/router";
import { BootAuth } from "@/features/auth/BootAuth";

export default function App() {
  return (
    <Providers>
      <BootAuth />
      <RouterProvider router={router} />
    </Providers>
  );
}
