import { ForbiddenException } from '@nestjs/common';
import { GlobalRole } from '@prisma/client';
import { AuthUser } from '../types/auth-user';

export function isGlobalAdmin(user: AuthUser): boolean {
  return user.role === GlobalRole.ADMIN;
}

export function membershipFor(user: AuthUser, organizationId: string) {
  return user.memberships.find((m) => m.organizationId === organizationId);
}

export function requireOrgMember(user: AuthUser, organizationId: string) {
  if (isGlobalAdmin(user)) {
    return;
  }
  if (!membershipFor(user, organizationId)) {
    throw new ForbiddenException('You are not a member of this organization');
  }
}

export function canManageOrganization(user: AuthUser, organizationId: string): boolean {
  if (isGlobalAdmin(user)) {
    return true;
  }
  const membership = membershipFor(user, organizationId);
  return (
    membership?.role === GlobalRole.ADMIN ||
    membership?.role === GlobalRole.INSTRUCTOR
  );
}

export function requireOrgManager(user: AuthUser, organizationId: string) {
  if (!canManageOrganization(user, organizationId)) {
    throw new ForbiddenException(
      'Instructor or admin access is required for this organization',
    );
  }
}

export function canInspectSubmissions(user: AuthUser, organizationId: string): boolean {
  return canManageOrganization(user, organizationId);
}
