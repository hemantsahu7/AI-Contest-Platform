import { ApiProperty } from '@nestjs/swagger';
import { GlobalRole } from '@prisma/client';
import { IsEnum, IsUUID } from 'class-validator';

export class AddMemberDto {
  @ApiProperty()
  @IsUUID('all')
  userId!: string;

  @ApiProperty({ enum: GlobalRole })
  @IsEnum(GlobalRole)
  role!: GlobalRole;
}
