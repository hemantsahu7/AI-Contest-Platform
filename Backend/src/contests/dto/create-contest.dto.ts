import { ApiProperty, ApiPropertyOptional } from '@nestjs/swagger';
import { ContestStatus } from '@prisma/client';
import { IsDateString, IsEnum, IsOptional, IsString, IsUUID, MinLength } from 'class-validator';

export class CreateContestDto {
  @ApiProperty()
  @IsString()
  @MinLength(3)
  title!: string;

  @ApiProperty()
  @IsString()
  description!: string;

  @ApiProperty()
  @IsUUID('all')
  organizationId!: string;

  @ApiProperty()
  @IsDateString()
  startTime!: string;

  @ApiProperty()
  @IsDateString()
  endTime!: string;

  @ApiPropertyOptional({ enum: ContestStatus })
  @IsOptional()
  @IsEnum(ContestStatus)
  status?: ContestStatus;
}
