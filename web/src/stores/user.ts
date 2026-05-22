import { create } from "zustand";
import { persist } from "zustand/middleware";
import type { User } from "@/types";

interface UserState {
  user: User | null;
  isIdentified: boolean;
  setUser: (user: User) => void;
  clearUser: () => void;
}

export const useUserStore = create<UserState>()(
  persist(
    (set) => ({
      user: null,
      isIdentified: false,
      setUser: (user) => set({ user, isIdentified: true }),
      clearUser: () => set({ user: null, isIdentified: false }),
    }),
    { name: "documind-user" }
  )
);
