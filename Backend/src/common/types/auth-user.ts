import { GlobalRole } from '@prisma/client';

export type MembershipInfo = {
  organizationId: string;
  role: GlobalRole;
};

export type AuthUser = {
  id: string;
  username: string;
  email: string;
  role: GlobalRole;
  memberships: MembershipInfo[];
};
